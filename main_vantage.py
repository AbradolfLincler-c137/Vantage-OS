import sys
sys.stdout.reconfigure(encoding='utf-8')

import json
import logging
import os
import subprocess
import time
import uuid
import winsound
import threading
import queue
from typing import Optional

# ---------------------------------------------------------------------------
# Absolute paths - works regardless of CWD
# ---------------------------------------------------------------------------
PROJECT_ROOT  = os.path.dirname(os.path.abspath(__file__))   # E:\hackathon projects\Orbit
BRIDGE_FILE   = os.path.join(PROJECT_ROOT, "task_bridge.json")
ENGINE_SCRIPT = os.path.join(PROJECT_ROOT, "run_engine.py")

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
POLL_INTERVAL_S       = 0.3  # seconds (enhanced from 1s)
ENGINE_STARTUP_WAIT_S = 3    # seconds to wait for Playwright to initialise
MAX_ACTOR_LOOPS       = 25   # safety cap: max Actor decisions per step
MAX_HEAL_RETRIES      = 3    # max consecutive failures before skipping a step

# ---------------------------------------------------------------------------
# Logging - file + stdout
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(PROJECT_ROOT, "vantage.log")),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("CEO")


# ---------------------------------------------------------------------------
# Bridge helpers
# ---------------------------------------------------------------------------

def _write_bridge(action) -> str:
    """
    Serialize an ActionSchema to task_bridge.json with status='pending'.
    Returns the generated task_id.
    """
    task_id = str(uuid.uuid4())[:8]

    scroll_amount = action.scroll_amount if action.scroll_amount is not None else 500
    if action.action_type == "scroll" and "up" in action.target_description.lower():
        scroll_amount = -abs(scroll_amount)

    payload = {
        "task_id":      task_id,
        "action_type":  action.action_type,
        "selector_type": action.selector_type,
        "target":       action.target_description,
        "text":         action.text or "",
        "scroll_amount": scroll_amount,
        "status":       "pending",
        "error":        "",
    }

    with open(BRIDGE_FILE, "w") as f:
        json.dump(payload, f, indent=2)

    logger.info(
        f"[Bridge ^] id={task_id} | action={action.action_type} "
        f"| selector={action.selector_type} | target='{action.target_description}'"
        + (f" | text='{action.text}'" if action.text else "")
    )
    return task_id


def _poll_bridge(task_id: str, timeout_s: int = 90) -> dict:
    """
    Poll task_bridge.json every POLL_INTERVAL_S until the engine responds
    with a status other than 'pending'.

    Returns the final bridge dict.
    Raises TimeoutError if no response within timeout_s.
    """
    deadline = time.time() + timeout_s
    spinner_chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
    spinner_idx = 0

    while time.time() < deadline:
        try:
            with open(BRIDGE_FILE, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            time.sleep(POLL_INTERVAL_S)
            continue

        if data.get("task_id") != task_id:
            time.sleep(POLL_INTERVAL_S)
            continue

        status = data.get("status", "pending")
        
        if status == "pending":
            msg = "Thinking..."
        elif status == "uploading":
            msg = "Analyzing Screen..."
        elif status == "executing":
            msg = "Taking Action..."
        else:
            # Clear UI line when returning
            sys.stdout.write("\r" + " " * 60 + "\r")
            sys.stdout.flush()
            return data

        sys.stdout.write(f"\r{spinner_chars[spinner_idx % len(spinner_chars)]} [Vantage-OS] {msg}")
        sys.stdout.flush()
        spinner_idx += 1
        
        time.sleep(POLL_INTERVAL_S)
        
    sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()
    raise TimeoutError(f"Engine did not respond to task_id={task_id} within {timeout_s}s.")


def _await_human_approval() -> None:
    """Block until a human sets task_bridge.json status to 'approved'."""
    logger.warning(
        "[CEO] Safety Gate - awaiting human approval.\n"
        "       Set task_bridge.json -> \"status\": \"approved\" to continue."
    )
    while True:
        time.sleep(POLL_INTERVAL_S)
        try:
            with open(BRIDGE_FILE, "r") as f:
                bridge = json.load(f)
            if bridge.get("status") == "approved":
                logger.info("[CEO] Human approval received. Resuming.")
                return
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CEO Loop
# ---------------------------------------------------------------------------

def start_engine() -> subprocess.Popen:
    """
    Spawns the Playwright engine and waits for the READY signal.
    """
    # Force initialize the bridge to 'initializing'
    try:
        with open(BRIDGE_FILE, "r") as f:
            bridge_data = json.load(f)
        bridge_data["status"] = "initializing"
        bridge_data["error"]  = ""
        with open(BRIDGE_FILE, "w") as f:
            json.dump(bridge_data, f, indent=2)
    except Exception:
        with open(BRIDGE_FILE, "w") as f:
            json.dump({"status": "initializing"}, f)

    python_exe = sys.executable
    logger.info(f"[CEO] Spawning engine: {python_exe} {ENGINE_SCRIPT}")

    engine_proc = subprocess.Popen(
        [python_exe, ENGINE_SCRIPT],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    logger.info("[CEO] Handshake started. Waiting for Engine to signal READY...")
    timeout = 40
    start = time.time()
    while time.time() - start < timeout:
        try:
            with open(BRIDGE_FILE, "r") as f:
                if json.load(f).get("status") == "ready":
                    logger.info("[CEO] Handshake successful. Engine is READY.")
                    return engine_proc
        except:
            pass
        time.sleep(0.1)

    logger.error("[CEO] Handshake TIMEOUT. Engine failed to reach READY state.")
    try:
        log_file = os.path.join(PROJECT_ROOT, "vantage.log")
        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                lines = f.readlines()
                logger.error("--- Last 10 lines of vantage.log ---")
                for line in lines[-10:]:
                    logger.error(line.strip())
                logger.error("------------------------------------")
    except Exception as e:
        logger.error(f"[CEO] Could not read vantage.log: {e}")

    if engine_proc.poll() is None:
        engine_proc.terminate()
    raise RuntimeError("Engine failed to reach READY state.")


def run_mission(goal: str) -> None:
    """
    Full orchestration for a single mission: Planner -> Actor Loop -> Engine IPC.
    """
    from logic.planner import Planner
    from logic.actor   import Actor

    # Reset bridge for new mission
    try:
        with open(BRIDGE_FILE, "r") as f:
            bridge_data = json.load(f)
        bridge_data["status"] = "pending"
        bridge_data["error"]  = ""
        with open(BRIDGE_FILE, "w") as f:
            json.dump(bridge_data, f, indent=2)
    except Exception:
        pass

    mission_finalized = False
    try:
        # -- 2. Generate strategic plan -----------------------------------
        logger.info(f"[CEO] Mission received: '{goal}'")
        planner = Planner()
        
        _MAX_PLAN_ATTEMPTS = 3
        steps = None
        for _attempt in range(1, _MAX_PLAN_ATTEMPTS + 1):
            try:
                steps = planner.create_plan(goal)
                break
            except Exception as _plan_err:
                logger.warning(f"[RECOVERY] Planner attempt {_attempt}/{_MAX_PLAN_ATTEMPTS} failed: {_plan_err}")
                if _attempt < _MAX_PLAN_ATTEMPTS:
                    time.sleep(5)
                else:
                    raise RuntimeError("Planner failed after max attempts.") from _plan_err

        total = len(steps)
        logger.info(f"[CEO] Plan ready - {total} steps.")

        # -- 3. Execute each step -----------------------------------------
        actor = Actor()
        current_url = "about:blank"
        thought_history = []
        
        for step_idx, step in enumerate(steps): # index used for Actor context
            divider = "=" * 64
            logger.info(f"\n{divider}\n[CEO] STEP {step_idx + 1}/{total}: {step}\n{divider}")

            last_error: Optional[str] = None
            heal_count = 0
            actor_loops = 0

            while actor_loops < MAX_ACTOR_LOOPS:
                actor_loops += 1
                
                try:
                    action = actor.determine_action(
                        goal=goal, 
                        full_plan=steps, 
                        current_step_idx=step_idx, 
                        current_url=current_url,
                        last_error=last_error,
                        thought_history=thought_history
                    )
                except Exception as e:
                    logger.error(f"[CEO] Actor error: {e}")
                    time.sleep(1)
                    continue

                if action.action_type.lower() == "done":
                    logger.info(f"[CEO] Step {step_idx} complete. Thought: {action.thought}")
                    thought_history.append(action.thought)
                    break

                task_id = _write_bridge(action)
                
                try:
                    result = _poll_bridge(task_id)
                except TimeoutError as e:
                    logger.error(f"[CEO] TIMEOUT: {e}")
                    last_error = str(e)
                    heal_count += 1
                    if heal_count >= MAX_HEAL_RETRIES: break
                    continue

                status = result.get("status", "")
                error = result.get("error", "")
                current_url = result.get("current_url", current_url)

                if status == "completed":
                    logger.info("[CEO] Action completed.")
                    thought_history.append(action.thought)
                    last_error = None
                    heal_count = 0
                elif status == "failed":
                    heal_count += 1
                    last_error = error or "Unknown engine error."
                    logger.warning(f"[CEO] Action failed ({heal_count}/{MAX_HEAL_RETRIES}): {last_error}")
                    if heal_count >= MAX_HEAL_RETRIES: break
                elif status == "WAIT_FOR_HUMAN":
                    print("\n[!!!] CAPTCHA DETECTED. PLEASE SOLVE IN BROWSER.")
                    winsound.Beep(1000, 500)
                    input("Mission Paused. Solve CAPTCHA and press Enter to Resume...")
                    with open(BRIDGE_FILE, "r") as f:
                        data = json.load(f)
                    data["status"] = "pending"
                    with open(BRIDGE_FILE, "w") as f:
                        json.dump(data, f, indent=2)
                    last_error = "CAPTCHA was present and solved by human."
                elif status == "awaiting_approval":
                    _await_human_approval()
            else:
                logger.warning(f"[CEO] Step {step_idx} hit Actor loop cap ({MAX_ACTOR_LOOPS}).")

        logger.info("[CEO] Mission complete.")
        mission_finalized = True

    except Exception as e:
        logger.error(f"[CEO] Fatal error in mission: {e}")


def shutdown_engine(engine_proc) -> None:
    if engine_proc and engine_proc.poll() is None:
        logger.info(f"[CEO] Terminating engine (PID {engine_proc.pid})...")
        engine_proc.terminate()
        try:
            engine_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            engine_proc.kill()
    logger.info("[CEO] Engine offline.")


if __name__ == "__main__":
    engine_proc = None
    input_q = queue.Queue()
    
    def _read_kbd():
        while True:
            try:
                line = input()
                input_q.put(line)
            except EOFError:
                break
                
    try:
        engine_proc = start_engine()
        t = threading.Thread(target=_read_kbd, daemon=True)
        t.start()
        
        while True:
            print("\n" + "=" * 64)
            print("[VANTAGE] Waiting for next mission... (Use browser UI or type here, 'q' to quit, 'manual' to pause)")
            print("=" * 64)
            
            mission = None
            
            while True:
                # 1. Check Terminal UI
                try:
                    mission = input_q.get_nowait().strip()
                except queue.Empty:
                    pass
                
                # 2. Check Browser UI
                if not mission:
                    try:
                        with open(BRIDGE_FILE, "r") as f:
                            data = json.load(f)
                        if data.get("status") == "new_mission":
                            mission = data.get("mission", "").strip()
                            if mission:
                                print(f"\n[CEO] Received Mission from Browser UI: {mission}")
                                data["status"] = "pending"
                                data["action_type"] = ""
                                with open(BRIDGE_FILE, "w") as f:
                                    json.dump(data, f, indent=2)
                    except Exception:
                        pass
                
                if mission:
                    break
                time.sleep(POLL_INTERVAL_S)
                
            if mission.lower() == 'q': break
            
            if mission.lower() == 'manual':
                logger.info("[CEO] Manual Mode: Pausing AI...")
                try:
                    with open(BRIDGE_FILE, "r") as f:
                        data = json.load(f)
                    data["status"] = "WAIT_FOR_HUMAN"
                    with open(BRIDGE_FILE, "w") as f:
                        json.dump(data, f, indent=2)
                except: pass
                print("[VANTAGE] AI Paused. Use browser normally. Type 'resume' into terminal to loop back...")
                continue
            
            if mission.lower() == 'resume':
                # Resume handled implicitly by looping
                continue
                
            run_mission(mission)

    except KeyboardInterrupt:
        logger.info("[CEO] Interrupted by operator.")
    finally:
        if engine_proc:
            shutdown_engine(engine_proc)


