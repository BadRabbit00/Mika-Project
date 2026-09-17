"""Fixed-camera PAD figures from immutable, outbox-persisted render inputs."""

import io
import json
from dataclasses import dataclass
from functools import lru_cache
from itertools import product
from math import isfinite
from pathlib import Path
from threading import Lock

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure
from matplotlib.patches import Patch
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from ruamel.yaml import YAML

SIGNS = tuple("".join(signs) for signs in product("+-", repeat=3))
RENDER_VERSION = 1
_render_lock = Lock()


@dataclass(frozen=True)
class PadPlot:
    style: dict
    names: dict

    @classmethod
    def from_config(cls, directory=Path("config")):
        directory = Path(directory)
        yaml = YAML(typ="safe")
        return cls(
            yaml.load(directory / "pad_plot.yaml"),
            {
                key: value["name"]
                for key, value in yaml.load(directory / "mood.yaml")["octants"].items()
            },
        )

    def snapshot(self, mood):
        values = {axis: float(mood[axis]) for axis in ("P", "A", "D")}
        if any(not isfinite(value) or abs(value) > 1 for value in values.values()):
            raise ValueError("PAD coordinates must be finite and within [-1, 1]")
        values = {axis: round(value, 4) for axis, value in values.items()}
        result = {
            "version": RENDER_VERSION,
            "mood": values,
            "vector": [values[axis] for axis in ("A", "D", "P")],
            "view": dict(self.style["view"]),
            "alpha": self.style["octant_alpha"],
            "tick_step": self.style["tick_step"],
            "octants": {
                key: {"name": self.names[key], "color": self.style["colors"][key]}
                for key in SIGNS
            },
        }
        _validate(result)
        return result


def _validate(snapshot):
    if snapshot["version"] != RENDER_VERSION:
        raise ValueError("Unsupported PAD render version")
    values = snapshot["mood"]
    if any(not isfinite(values[axis]) or abs(values[axis]) > 1 for axis in "PAD"):
        raise ValueError("Invalid PAD render coordinates")
    if snapshot["vector"] != [values[axis] for axis in "ADP"]:
        raise ValueError("PAD render axes must be X=A, Y=D, Z=P")
    if set(snapshot["octants"]) != set(SIGNS):
        raise ValueError("PAD rendering requires all eight octants")
    for item in snapshot["octants"].values():
        to_rgba(item["color"])
        if not item["name"]:
            raise ValueError("An octant name is missing")
    view = snapshot["view"]
    if (
        view["vertical_axis"] != "z"
        or view["roll"] != 0
        or view["projection"] != "ortho"
        or not 0 < view["elevation"] < 90
        or not isfinite(view["azimuth"])
        or not 0 < snapshot["alpha"] < 0.5
        or snapshot["tick_step"] not in (0.25, 0.5, 1)
    ):
        raise ValueError("Invalid PAD view; Z must remain upright")


def _cube(signs):
    bounds = [(0, 1) if sign == "+" else (-1, 0) for sign in signs]
    # Faces are independent of the current mood and the camera never follows it.
    faces = []
    for fixed in range(3):
        other = [axis for axis in range(3) if axis != fixed]
        for end in bounds[fixed]:
            face = []
            for left, right in ((0, 0), (1, 0), (1, 1), (0, 1)):
                vertex = [0.0, 0.0, 0.0]
                vertex[fixed] = end
                vertex[other[0]] = bounds[other[0]][left]
                vertex[other[1]] = bounds[other[1]][right]
                face.append(vertex)
            faces.append(face)
    return faces


def make_figure(snapshot):
    """Build a standalone Agg figure; the caller owns and clears the figure."""
    _validate(snapshot)
    figure = Figure(figsize=(11, 8), dpi=150, facecolor="white")
    FigureCanvasAgg(figure)
    axes = figure.add_axes(
        (0.01, 0.09, 0.73, 0.80), projection="3d", computed_zorder=False
    )
    view = snapshot["view"]
    axes.view_init(
        elev=view["elevation"], azim=view["azimuth"], roll=0, vertical_axis="z"
    )
    axes.set_proj_type("ortho")
    axes.set_box_aspect((1, 1, 1))
    axes.set(xlim=(-1.2, 1.2), ylim=(-1.2, 1.2), zlim=(-1.2, 1.2))
    axes.set(xlabel="A · Arousal", ylabel="D · Control", zlabel="P · Emotional tone")
    axes.set_axis_off()

    legend = []
    for key, item in snapshot["octants"].items():
        # The mood mapping uses P,A,D; the figure uses X=A, Y=D, Z=P.
        xyz_signs = (key[1], key[2], key[0])
        color = item["color"]
        axes.add_collection3d(
            Poly3DCollection(
                _cube(xyz_signs),
                facecolors=to_rgba(color, snapshot["alpha"]),
                edgecolors=to_rgba(color, 0.22),
                linewidths=0.5,
                zorder=1,
            )
        )
        center = [0.72 if sign == "+" else -0.72 for sign in xyz_signs]
        axes.text(
            *center,
            item["name"].replace("_", "\n"),
            ha="center",
            va="center",
            fontsize=8,
            color="#343434",
            zorder=5,
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.6, "pad": 1},
        )
        legend.append(
            Patch(facecolor=to_rgba(color, 0.6), label=item["name"].replace("_", " "))
        )

    vector = np.asarray(snapshot["vector"])
    for index, label in enumerate(("A · Arousal", "D · Control", "P · Emotional tone")):
        unit = np.eye(3)[index]
        axes.plot(
            *np.array([-1.1 * unit, 1.12 * unit]).T, color="#777777", lw=1, zorder=2
        )
        axes.quiver(
            *(1.06 * unit),
            *(0.10 * unit),
            color="#777777",
            arrow_length_ratio=0.65,
            zorder=3,
        )
        axes.text(*(1.22 * unit), label, ha="center", fontsize=10, zorder=6)
        ticks = np.arange(-1, 1.01, snapshot["tick_step"])
        tick_direction = np.eye(3)[(index + 1) % 3]
        for value in ticks:
            if abs(value) < 0.001:
                continue
            center = value * unit
            ends = [center - 0.025 * tick_direction, center + 0.025 * tick_direction]
            axes.plot(*np.array(ends).T, color="#777777", lw=0.8, zorder=3)
            axes.text(
                *(center + 0.08 * tick_direction),
                f"{value:g}",
                fontsize=7,
                color="#555555",
                zorder=6,
            )
        # Solid segments on each axis show the signed component without dashed guides.
        end = vector[index] * unit
        axes.plot(*np.array([np.zeros(3), end]).T, color="#3a3a3a", lw=3, zorder=4)
        axes.scatter(*end, color="black", s=15, depthshade=False, zorder=5)
    axes.scatter(0, 0, 0, color="black", s=20, depthshade=False, zorder=6)
    if np.any(vector):
        axes.quiver(
            0,
            0,
            0,
            *vector,
            color="black",
            linewidth=2.8,
            arrow_length_ratio=0.14,
            zorder=7,
        )
    axes.scatter(*vector, color="black", s=35, depthshade=False, zorder=8)
    figure.text(0.07, 0.94, "Mika · PAD", fontsize=20, weight="bold", color="#202020")
    figure.text(0.07, 0.90, "X = A     Y = D     Z = P", fontsize=11, color="#555555")
    figure.legend(
        handles=legend,
        loc="center left",
        bbox_to_anchor=(0.76, 0.51),
        frameon=False,
        fontsize=10,
        labelspacing=1.3,
    )
    coordinates = "     ".join(
        f"{axis} = {snapshot['mood'][axis]:+.4f}" for axis in "PAD"
    )
    figure.text(
        0.07, 0.055, coordinates, fontsize=14, family="DejaVu Sans Mono", color="black"
    )
    return figure, axes


@lru_cache(maxsize=4)
def _cached_png(serialized):
    figure, _ = make_figure(json.loads(serialized))
    try:
        output = io.BytesIO()
        figure.savefig(
            output, format="png", facecolor="white", metadata={"Software": "BlogAI PAD"}
        )
        return output.getvalue()
    finally:
        figure.clear()


def render_png(snapshot):
    """Render outside the event loop and any database transaction."""
    serialized = json.dumps(snapshot, sort_keys=True, allow_nan=False)
    # Matplotlib has shared font caches even when using separate Figure objects.
    with _render_lock:
        return _cached_png(serialized)
