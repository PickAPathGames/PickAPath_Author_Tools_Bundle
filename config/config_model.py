# config/config_model.py
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional


@dataclass
class Goal:
    name: str
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    meta: Dict[str, Any] = field(default_factory=dict)
    files: List[str] = field(default_factory=list)
    vars: Dict[str, Any] = field(default_factory=dict)
    save_vars: List[str] = field(default_factory=list)
    goals: Dict[str, Goal] = field(default_factory=dict)
    map_exclude: set = field(default_factory=set)

    def __repr__(self):
        return f"<Config files={self.files!r} vars={list(self.vars.keys())!r}>"

    def add_goal(self, goal_name: str):
        if goal_name in self.goals:
            return self.goals[goal_name]
        g = Goal(name=goal_name)
        self.goals[goal_name] = g
        return g

