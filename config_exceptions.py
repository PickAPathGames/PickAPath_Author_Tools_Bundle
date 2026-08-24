# config_exceptions.py

class ConfigSyntaxError(Exception):
    """Used when the parser encounters invalid config syntax."""
    def __init__(self, message, line=None):
        self.line = line
        super().__init__(message)

    def __str__(self):
        if self.line is not None:
            return f"[line {self.line}] {super().__str__()}"
        return super().__str__()
