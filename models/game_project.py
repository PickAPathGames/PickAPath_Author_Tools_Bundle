# models/game_project.py

class GameProject:
    def __init__(self, title, author, files, variables, initial_vars,
                 save_vars, goals, scenes, start_scene, start_tag, indent=2, meta=None, map_exclude=None):

        self.title = title
        self.author = author
        self.files = files
        self.variables = variables
        self.initial_vars = initial_vars
        self.save_vars = save_vars
        self.goals = goals
        self.scenes = scenes
        self.start_scene = start_scene
        self.start_tag = start_tag
        self.indent = indent
        self.meta = meta if meta is not None else {}
        self.map_exclude = map_exclude if map_exclude is not None else set()

    def get_scene(self, name):
        return self.scenes.get(name)

    def get(self, key, default=None):
        if key == "files":
            return self.files
        elif key == "vars":
            return self.variables
        elif key == "meta":
            return self.meta
        elif key == "save_vars":
            return self.save_vars
        elif key == "goals":
            return self.goals
        elif key == "map_exclude":
            return self.map_exclude
        return default

