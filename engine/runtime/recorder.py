# engine/runtime/recorder.py
from engine.runtime.replay_errors import ReplayExhausted, ReplayDesync


class PickRecorder:
    """
    Records author choices and entropy deterministically.
    """

    def __init__(self):
        self._current_pick = None
        self.choices = []        # ordered list of choice_id strings
        self.entropy = []        # ordered list of {"entropy_id": str, "value": int}
        self.rewind_points = []

    def on_pick(self, pick_id):
        if self._current_pick is not None:
            raise RuntimeError("Nested pick detected")
        self._current_pick = pick_id
        self.rewind_points.append({
            "choice_index": len(self.choices),
            "entropy_index": len(self.entropy),
        })

    def on_choose(self, pick_id, choice_id):
        if pick_id != self._current_pick:
            raise RuntimeError(
                f"Pick mismatch: expected {self._current_pick}, got {pick_id}"
            )
        self.choices.append(choice_id)
        self._current_pick = None

    def on_entropy(self, entropy_id, value):
        self.entropy.append({
            "entropy_id": entropy_id,
            "value": value,
        })

    def on_rewind(self):
        """Call this when the author rewinds history in play_cli."""
        if self.rewind_points:
            last_point = self.rewind_points.pop()
            self.choices = self.choices[: last_point["choice_index"]]
            self.entropy = self.entropy[: last_point["entropy_index"]]

    def export(self) -> dict:
        return {
            "version": 1,
            "choices": list(self.choices),
            "entropy": list(self.entropy),
        }


class PickReplay:
    """
    Replays a recorded session deterministically.

    Accepts either:
      - a full recording dict: {"choices": [...], "entropy": [...]}
      - just an entropy list:  [{"entropy_id": ..., "value": ...}, ...]

    The second form is what load_from_data passes when it only has the
    entropy_tape (choice replay is handled by session.load_from_data itself).
    """

    def __init__(self, recording):
        self.choice_index = 0
        self.entropy_index = 0

        if isinstance(recording, dict):
            # Full recording from PickRecorder.export()
            self.choices = recording.get("choices", [])
            self.entropy = recording.get("entropy", [])
        elif isinstance(recording, list):
            # Bare entropy tape from export_save_data
            self.choices = []
            self.entropy = recording
        else:
            self.choices = []
            self.entropy = []

        # Build a mapping: choice_index → entropy_index at that point.
        # Used by rewind_last() to restore the entropy cursor correctly.
        # Can't build this from just the tape without replaying, so
        # track it lazily as choices are consumed.
        self._entropy_checkpoints = [0]   # checkpoint[i] = entropy_index before choice i

    def next_choice(self, pick_id):
        try:
            choice_id = self.choices[self.choice_index]
        except IndexError:
            raise ReplayExhausted("Replay exhausted (choices)")
        self.choice_index += 1
        # Record where entropy stood at the START of this choice's execution
        self._entropy_checkpoints.append(self.entropy_index)
        return choice_id

    def next_entropy(self, entropy_id):
        try:
            entry = self.entropy[self.entropy_index]
        except IndexError:
            raise ReplayExhausted("Replay exhausted (entropy)")

        if entry["entropy_id"] != entropy_id:
            raise ReplayDesync(
                f"Entropy desync: expected '{entry['entropy_id']}', got '{entropy_id}'"
            )

        self.entropy_index += 1
        return entry["value"]

    def rewind_last(self):
        """Roll back the last consumed choice and its entropy."""
        if self.choice_index == 0:
            return False
        self.choice_index -= 1
        self.entropy_index = self._entropy_index_at_choice(self.choice_index)
        return True

    def _entropy_index_at_choice(self, choice_index: int) -> int:
        """
        Returns the entropy_index that was current just before choice_index
        was consumed, i.e. the safe rollback point for that choice.
        """
        if choice_index < len(self._entropy_checkpoints):
            return self._entropy_checkpoints[choice_index]
        # Fallback: don't have a checkpoint this far back, stay where we are
        return self.entropy_index

