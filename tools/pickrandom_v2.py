# tools/pickrandom_v2.py

import random
import copy
import json
import multiprocessing as mp
from collections import defaultdict
from simulator import Simulator
from tools.coverage_csv import CoverageCSVExporter
from tools.snapshot_table_csv_exporter import SnapshotTableCSVExporter

from tools.snapshot_stats import SnapshotStats
from tools.coverage_stats import CoverageStats
from tools.snapshot_csv import SnapshotCSVExporter
from tools.snapshot_json import SnapshotJSONExporter
from tools.branch_coverage import BranchCoverage



DEFAULT_CONFIG = {
    "seed": 12345,
    "iterations": 1000,
    "workers": 1,
    "loop_limit": 1000,

    "snapshots": {
        "enabled": True,
    },

    "raw_snapshots": {
        "enabled": False
    },

    "coverage": {
        "enabled": True
    }
}


def _run_worker(args):
    (
        scenes,
        start_scene,
        start_tag,
        iterations,
        seed,
        initial_variables,
        snapshot_vars,
        scene_order,
        collect_raw_snapshots,
        base_config,
    ) = args

    # clone config and override per-worker values
    config = copy.deepcopy(base_config)
    config["iterations"] = iterations
    config["seed"] = seed
    config["workers"] = 1  # critical: prevent recursive pools

    runner = PickRandomRunner(
        scenes=scenes,
        start_scene=start_scene,
        start_tag=start_tag,
        config=config,
        initial_variables=initial_variables,
        snapshot_vars=snapshot_vars,
        scene_order=scene_order,
    )

    return runner.run()



class PickRandomRunner:
    def __init__(
        self,
        scenes,
        start_scene,
        start_tag,
        config,
        initial_variables=None,
        snapshot_vars=None,
        scene_order=None,
        out_dir="out",
        debug=False,
    ):

        self.scenes = scenes
        # self.scene_order = list(scenes.keys())
        self.scene_order = scene_order or list(scenes.keys())
        self.start_scene = start_scene
        self.start_tag = start_tag
        self.initial_variables = initial_variables or {}
        self.snapshot_vars = snapshot_vars
        self.debug = debug

        # Aggregates
        self.snapshots_by_tag = defaultdict(list)
        self.visited_nodes = defaultdict(int)
        self.all_snapshots = []
        self.errors = {}
        self.out_dir = out_dir

        self.config = config
        self.iterations = config["iterations"]
        self.seed = config["seed"]

        self.snapshots_enabled = config["snapshots"]["enabled"]
        self.collect_raw_snapshots = config["raw_snapshots"]["enabled"]
        self.coverage_enabled = config["coverage"]["enabled"]
        self.seed_mode = self.config.get("seed_mode", "random")
        self.seed_offset = self.config.get("seed_offset", 0)

        self.branch_coverage = BranchCoverage()


    # --------------------------------------------------

    def run(self):
        workers = self.config.get("workers", 1)

        if workers <= 1:
            return self._run_single()

        return self._run_parallel(workers)

    def _run_single(self):
        print(f"[PICK] Starting {self.iterations} iterations")
        # print(f"[PICK] Seed = {self.seed}")
        print(
            f"[PICK] Iterations = {self.iterations} | "
            f"Seed mode = {self.seed_mode} | "
            f"Seed range = [{self.seed_offset} .. {self.seed_offset + self.iterations - 1}]"
        )

        for i in range(self.iterations):
            seed_index = self.seed_offset + i

            if self.seed_mode == "linear":
                iter_seed = seed_index
            else:
                # stable, decorrelated, reproducible
                iter_seed = hash((self.seed, seed_index)) & 0xFFFFFFFF

            sim = Simulator(
                scenes=self.scenes,
                seed=iter_seed,
                config={
                    "vars": copy.deepcopy(self.initial_variables),
                    "scene_order": self.scene_order,
                    "loop_limit": self.config.get("loop_limit", 500),
                },
                branch_coverage=self.branch_coverage,
            )

            result = sim.simulate_once(
                start_scene=self.start_scene,
                start_tag=self.start_tag,
                initial_vars=copy.deepcopy(self.initial_variables),
            )

            # if i == 0: # Just check the first iteration
            #     print(f"DEBUG: First run path length: {len(result['visited_nodes'])}")
            #     print(f"DEBUG: First run nodes: {list(result['visited_nodes'].keys())}...")
            #     # print(f"DEBUG: First run nodes: {list(result['visited_nodes'])}...")


            self._collect(result)

        # self._export()
        return self._final_result()

    def _run_parallel(self, workers):
        print(f"[PICK] Starting {self.iterations} iterations with {workers} workers")
        # print(f"[PICK] Seed = {self.seed}")
        print(
            f"[PICK] Iterations = {self.iterations} | "
            f"Seed mode = {self.seed_mode} | "
            f"Seed range = [{self.seed_offset} .. {self.seed_offset + self.iterations - 1}]"
        )


        base_rng = random.Random(self.seed)

        counts = [self.iterations // workers] * workers
        for i in range(self.iterations % workers):
            counts[i] += 1

        worker_args = []

        current_seed = self.seed_offset

        for count in counts:
            worker_config = copy.deepcopy(self.config)
            worker_config["iterations"] = count
            worker_config["workers"] = 1
            worker_config["seed_offset"] = current_seed

            worker_args.append((
                self.scenes,
                self.start_scene,
                self.start_tag,
                count,
                worker_config["seed"],   # base seed (used only for random mode)
                self.initial_variables,
                self.snapshot_vars,
                self.scene_order,
                self.collect_raw_snapshots,
                worker_config,
            ))

            current_seed += count

        with mp.Pool(processes=workers) as pool:
            results = pool.map(_run_worker, worker_args)

        for res in results:
            self._merge_result(res)

        # self._export()
        return self._final_result()

    def _merge_result(self, result):
        for key, vals in result["snapshots"].items():
            self.snapshots_by_tag[key].extend(vals)

        for key, count in result["visited_nodes"].items():
            self.visited_nodes[key] += count

        for err in result["errors"]:
            if err not in self.errors:
                self.errors[err] = err
        
        if "branch_coverage" in result:
            self.branch_coverage.merge(result["branch_coverage"])



    # --------------------------------------------------

    def _collect(self, result):
        # Snapshots
        if self.snapshots_enabled:
            for snap in result["snapshots"]:
                scene = snap["scene"]
                tag = snap["tag"]
                vars_ = snap["vars"]

                if self.snapshot_vars:
                    vars_ = {k: vars_.get(k) for k in self.snapshot_vars}

                self.snapshots_by_tag[(scene, tag)].append(vars_)

        # Visited nodes
        for key, count in result["visited_nodes"].items():
            self.visited_nodes[key] += count

        # raw snapshots (optional)
        if self.collect_raw_snapshots:
            self.all_snapshots.extend(result["snapshots"])
        
        # Errors (global deduplication)
        for err in result.get("errors", []):
            key = err  # error string is already stable & formatted
            if key not in self.errors:
                self.errors[key] = err


    # --------------------------------------------------

    def _final_result(self):

        return {
            "snapshots": dict(self.snapshots_by_tag),
            "visited_nodes": dict(self.visited_nodes),
            "branch_coverage": self.branch_coverage,
            "errors": list(self.errors.values()),
            "iteration_count": self.iterations,
        }


def load_pickrandom_config(path):
    with open(path, "r") as f:
        user = json.load(f)
    return deep_merge(DEFAULT_CONFIG, user)

def deep_merge(base, override):
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ------------------------------------------------------
# Convenience wrapper
# ------------------------------------------------------

def run_pickrandom(
    scenes,
    start_scene,
    start_tag,
    config_path,
    initial_variables=None,
    snapshot_vars=None,
    debug=False,
    scene_order=None,
):

    config = load_pickrandom_config(config_path)

    runner = PickRandomRunner(
        scenes=scenes,
        start_scene=start_scene,
        start_tag=start_tag,
        config=config,
        initial_variables=initial_variables,
        snapshot_vars=snapshot_vars,
        debug=debug,
        scene_order=scene_order,
    )
    return runner.run()



