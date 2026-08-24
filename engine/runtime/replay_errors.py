# engine/runtime/replay_errors.py

class ReplayError(RuntimeError):
    pass

class ReplayExhausted(ReplayError):
    pass

class ReplayDesync(ReplayError):
    pass
