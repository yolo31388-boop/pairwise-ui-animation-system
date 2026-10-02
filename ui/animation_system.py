"""
pairwise-ui-animation-system - UIAnimationSystem

Provides:
- tween_animation: eased tweens with delay, loop and yoyo (ping-pong) support
- transition_page: directional page transitions (opacity + translate + scale)
  with a queue so rapid navigation never overlaps animations
- evaluate_curve: preset easing curves and custom cubic-bezier curves,
  duration-independent (time-scaled) evaluation with per-frame caching
- update_animation_state: per-element/per-animation state management with
  pause/resume that preserves position and completion callbacks
- optimize_animation: GPU-friendly styles (transform instead of left/top,
  will-change hints) and frame throttling to avoid layout thrashing
"""

from __future__ import annotations

import time as _time
from collections import deque


EASING_PRESETS = {
    "linear": (0.0, 0.0, 1.0, 1.0),
    "ease": (0.25, 0.1, 0.25, 1.0),
    "ease-in": (0.42, 0.0, 1.0, 1.0),
    "ease-out": (0.0, 0.0, 0.58, 1.0),
    "ease-in-out": (0.42, 0.0, 0.58, 1.0),
}

# (x multiplier, y multiplier) describing where an entering page slides in from
_DIRECTION_VECTORS = {
    "forward": (1.0, 0.0),
    "back": (-1.0, 0.0),
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
    "up": (0.0, -1.0),
    "down": (0.0, 1.0),
}


def _bezier_axis(p1, p2, t):
    """Value of one axis of a cubic bezier with fixed endpoints (0,0)->(1,1)."""
    mt = 1.0 - t
    return 3.0 * mt * mt * t * p1 + 3.0 * mt * t * t * p2 + t * t * t


def _bezier_axis_derivative(p1, p2, t):
    mt = 1.0 - t
    return (
        3.0 * mt * mt * p1
        + 6.0 * mt * t * (p2 - p1)
        + 3.0 * t * t * (1.0 - p2)
    )


def _solve_cubic_bezier(x1, y1, x2, y2, x):
    """Solve the cubic-bezier curve: given x return y (both in [0, 1])."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    t = x
    for _ in range(8):
        current_x = _bezier_axis(x1, x2, t)
        if abs(current_x - x) < 1e-6:
            return _bezier_axis(y1, y2, t)
        derivative = _bezier_axis_derivative(x1, x2, t)
        if abs(derivative) < 1e-6:
            break
        t = min(max(t - (current_x - x) / derivative, 0.0), 1.0)

    lo, hi = 0.0, 1.0
    while hi - lo > 1e-6:
        if _bezier_axis(x1, x2, t) < x:
            lo = t
        else:
            hi = t
        t = (lo + hi) / 2.0
    return _bezier_axis(y1, y2, t)


def _is_infinite_loop(loop):
    return loop is True or loop is None or (
        isinstance(loop, (int, float)) and not isinstance(loop, bool) and loop < 0
    )


def _css_length(value):
    if isinstance(value, (int, float)):
        return f"{value}px"
    return str(value)


class UIAnimationSystem:
    def __init__(self):
        # element -> animation name -> state dict (multiple animations per
        # element are keyed by name and never overwrite each other)
        self.state = {}
        self.curve_cache = {}
        self._transition_queue = deque()
        self._active_transition = None
        self._optimized_cache = {}
        self._last_frame = {}

    # ------------------------------------------------------------------
    # Easing curves
    # ------------------------------------------------------------------
    def _resolve_bezier(self, curve, points):
        if points is not None:
            return tuple(float(p) for p in points)
        if isinstance(curve, (tuple, list)) and len(curve) == 4:
            return tuple(float(p) for p in curve)
        name = str(curve).lower()
        return EASING_PRESETS.get(name, EASING_PRESETS["linear"])

    def evaluate_curve(self, curve="linear", t=0.0, duration=1.0,
                       points=None, use_cache=True):
        """Return the eased value for time ``t`` of an animation of ``duration``.

        Time is normalized (t / duration) so changing the duration never
        changes the curve shape. Preset names or a custom cubic-bezier
        (``points`` = x1, y1, x2, y2) are supported. Results are cached.
        """
        if duration <= 0:
            progress = 0.0 if t <= 0 else 1.0
        else:
            progress = min(max(t / float(duration), 0.0), 1.0)

        control = self._resolve_bezier(curve, points)
        cache_key = (control, round(progress, 6))
        if use_cache and cache_key in self.curve_cache:
            return self.curve_cache[cache_key]

        value = _solve_cubic_bezier(*control, x=progress)
        if use_cache:
            self.curve_cache[cache_key] = value
        return value

    # ------------------------------------------------------------------
    # Tween animation
    # ------------------------------------------------------------------
    def tween_animation(self, start=0.0, end=1.0, duration=1.0,
                        easing="ease-in-out", elapsed=0.0, loop=1,
                        yoyo=False, delay=0.0, points=None, **kwargs):
        """Eased interpolation from ``start`` to ``end``.

        Supports an easing curve, a start ``delay``, finite/infinite
        ``loop`` counts and ``yoyo`` (alternating / ping-pong) playback.
        """
        if "time" in kwargs:
            elapsed = kwargs["time"]
        if "t" in kwargs:
            elapsed = kwargs["t"]

        base_result = {
            "start": start,
            "end": end,
            "duration": duration,
            "easing": easing,
            "loop": loop,
            "yoyo": yoyo,
            "delay": delay,
            "points": points,
        }

        if elapsed < delay:
            result = dict(base_result)
            result.update({
                "value": start,
                "progress": 0.0,
                "iteration": 0,
                "direction": 1,
                "state": "delayed",
            })
            return result

        active = elapsed - delay
        if duration <= 0:
            iteration, cycle = 0, 1.0
        else:
            iteration = int(active // duration)
            cycle = (active % duration) / duration

        infinite = _is_infinite_loop(loop)
        finished = False
        if not infinite:
            loop_count = max(1, int(loop))
            if iteration >= loop_count:
                iteration = loop_count - 1
                cycle = 1.0
                finished = True

        direction = -1 if (yoyo and iteration % 2 == 1) else 1
        cycle = 1.0 - cycle if direction < 0 else cycle
        eased = self.evaluate_curve(easing, cycle, 1.0, points=points)
        value = start + (end - start) * eased

        result = dict(base_result)
        result.update({
            "value": value,
            "progress": eased,
            "iteration": iteration,
            "direction": direction,
            "state": "finished" if finished else "running",
        })
        return result

    # ------------------------------------------------------------------
    # Page transitions
    # ------------------------------------------------------------------
    def transition_page(self, from_page=None, to_page=None,
                        direction="forward", duration=0.3,
                        easing="ease-in-out", distance=1.0, **kwargs):
        """Build a directional page transition.

        Both entering and leaving pages animate opacity, translation and
        scale, and enter/exit move in opposite directions. If a transition
        is already running the new one is queued instead of overlapping.
        """
        dx, dy = _DIRECTION_VECTORS.get(direction, (1.0, 0.0))
        enter_from = (dx * distance, dy * distance)
        exit_to = (-dx * distance, -dy * distance)

        spec = {
            "from_page": from_page,
            "to_page": to_page,
            "direction": direction,
            "duration": duration,
            "easing": easing,
            "properties": ["opacity", "transform"],
            "enter": {
                "opacity": [0.0, 1.0],
                "translate": [enter_from, (0.0, 0.0)],
                "scale": [0.95, 1.0],
            },
            "exit": {
                "opacity": [1.0, 0.0],
                "translate": [(0.0, 0.0), exit_to],
                "scale": [1.0, 0.95],
            },
            "queue_length": len(self._transition_queue),
        }

        if self._active_transition is not None:
            self._transition_queue.append(spec)
            spec["queued"] = True
            spec["queue_position"] = len(self._transition_queue)
            spec["queue_length"] = len(self._transition_queue)
            return spec

        self._active_transition = spec
        spec["queued"] = False
        spec["queue_position"] = 0
        return spec

    def finish_transition(self):
        """Complete the active transition and start the next queued one."""
        finished = self._active_transition
        self._active_transition = (
            self._transition_queue.popleft() if self._transition_queue else None
        )
        return self._active_transition

    # ------------------------------------------------------------------
    # Animation state management
    # ------------------------------------------------------------------
    def update_animation_state(self, element="default", name="default",
                               action="update", progress=None, callback=None,
                               **properties):
        """Manage animation state for one animation on one element.

        Multiple animations of the same element live side by side (keyed by
        ``name``). Supports start/update/pause/resume/finish; pause keeps
        the current position and resume continues from it; completion fires
        every registered callback once.
        """
        element_states = self.state.setdefault(element, {})
        animation = element_states.setdefault(name, {
            "element": element,
            "name": name,
            "status": "running",
            "progress": 0.0,
            "paused_progress": None,
            "callbacks": [],
            "properties": {},
        })

        if callback is not None:
            animation["callbacks"].append(callback)

        if action in ("start", "restart"):
            animation["status"] = "running"
            if action == "restart":
                animation["progress"] = 0.0
                animation["paused_progress"] = None
            if progress is not None:
                animation["progress"] = progress
        elif action in ("update", "progress", None):
            if progress is not None:
                animation["progress"] = progress
        elif action == "pause":
            if progress is not None:
                animation["progress"] = progress
            animation["paused_progress"] = animation["progress"]
            animation["status"] = "paused"
        elif action == "resume":
            if animation["paused_progress"] is not None:
                animation["progress"] = animation["paused_progress"]
                animation["paused_progress"] = None
            animation["status"] = "running"
        elif action in ("finish", "complete"):
            if progress is not None:
                animation["progress"] = progress
            else:
                animation["progress"] = 1.0
            animation["paused_progress"] = None
            animation["status"] = "finished"
        elif action == "stop":
            if progress is not None:
                animation["progress"] = progress
            animation["paused_progress"] = None
            animation["status"] = "stopped"

        animation["properties"].update(properties)

        if animation["status"] in ("finished", "stopped"):
            for registered in list(animation["callbacks"]):
                registered(dict(animation))
            animation["callbacks"] = []

        return dict(animation)

    # ------------------------------------------------------------------
    # Performance optimization
    # ------------------------------------------------------------------
    def optimize_animation(self, styles=None, element="default", fps=60,
                           now=None):
        """Turn layout-triggering styles into GPU-friendly, throttled output.

        ``left``/``top`` are moved into a ``translate3d`` transform, a
        ``will-change`` hint and backface hint are added, and calls within
        one frame budget are throttled (the cached styles are returned).
        """
        if now is None:
            now = _time.monotonic()
        frame_budget = 1.0 / float(fps) if fps and fps > 0 else 0.0

        last = self._last_frame.get(element)
        if last is not None and frame_budget > 0 and (now - last) < frame_budget:
            throttled = dict(self._optimized_cache.get(element, {
                "styles": {}, "transform": "", "will_change": "",
                "accelerated": True, "hardware_accelerated": True,
                "fps": fps,
            }))
            throttled["throttled"] = True
            return throttled

        styles = dict(styles or {})
        offset_x = styles.pop("left", styles.pop("x", 0))
        offset_y = styles.pop("top", styles.pop("y", 0))

        translate = (
            f"translate3d({_css_length(offset_x)}, "
            f"{_css_length(offset_y)}, 0)"
        )
        existing_transform = styles.pop("transform", None)
        if existing_transform:
            transform = f"{translate} {existing_transform}"
        else:
            transform = translate
        styles["transform"] = transform
        styles["will-change"] = "transform, opacity"
        styles["backface-visibility"] = "hidden"
        styles["-webkit-transform"] = transform

        result = {
            "styles": styles,
            "transform": transform,
            "will_change": "transform, opacity",
            "accelerated": True,
            "hardware_accelerated": True,
            "throttled": False,
            "fps": fps,
        }
        self._last_frame[element] = now
        self._optimized_cache[element] = result
        return result
