"""
Copyright (c) 2026 Diego Millan - Pick A Path
Licensed under the Pick-A-Path Public License v1.0.
See LICENSE.txt in the project root for full license terms.
Commercial use without prior written consent is strictly prohibited.
"""


# command_registry.py
from typing import Dict, Callable, Any, Optional
import importlib
import pkgutil

class CommandRegistry:
    def __init__(self):
        self._parsers: Dict[str, Callable] = {}
        self._runtimes: Dict[str, Callable] = {}

    def is_valid_command(self, name: str) -> bool:
        """Checks if a command exists in either parser or runtime maps."""
        full_name = name if name.startswith("-") else f"-{name}"
        return full_name in self._parsers or full_name in self._runtimes

    def get_all_commands(self) -> set[str]:
        """Returns all registered command names."""
        return set(self._parsers.keys()) | set(self._runtimes.keys())

    def register_parser(self, name: str):
        """Standard Parser registration. Name MUST start with '-'."""
        def dec(f):
            self._parsers[name] = f
            return f
        return dec

    def register_runtime(self, name: str):
        """Standard Runtime registration. Name MUST start with '-'."""
        def dec(f):
            self._runtimes[name] = f
            return f
        return dec

    def create_block(self, name: str, parser, args: str, line_no: int, level: int):
        full_name = name if name.startswith("-") else f"-{name}"
        if full_name in self._parsers:
            return self._parsers[full_name](parser, args, line_no, level)
        # If no parser is found, create a generic data block
        return {"cmd": full_name, "args": args, "__line__": line_no, "__indent__": level}

    def run_runtime(self, name: str, engine, args: str, block: dict) -> Optional[Any]:
        if name in self._runtimes:
            return self._runtimes[name](engine, args, block)
        return None

COMMANDS = CommandRegistry()

def _auto_import_commands():
    targets = ["commands", "prod_commands"]
    for target in targets:
        try:
            package = importlib.import_module(target)
            for module_info in pkgutil.iter_modules(package.__path__):
                importlib.import_module(f"{target}.{module_info.name}")
        except ImportError:
            # ignore missing prod_commands
            pass

_auto_import_commands()


