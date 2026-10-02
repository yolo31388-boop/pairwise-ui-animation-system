"""
pairwise-ui-animation-system - UIAnimationSystem

A UI animation engine providing:
- tween_animation(): eased tweens with loop / yoyo (ping-pong) / delay
- transition_page(): page transitions with translate + scale + direction + queue
- evaluate_curve(): preset + custom cubic-bezier curves, time scaling, caching
- update_animation_state(): per-element multi-animation state, pause/resume, callbacks
- optimize_animation(): hardware-accelerated transform output, will-change, throttling
"""

import math
import time
import uuid


# Preset easing curves expressed as cubic-bezier control points (x1, y1, x2, y2).
EASING_PRESETS = {
    "linear": (0.0, 0.0, 1.0, 1.0),
    "ease": (0.25, 0.1, 0.25, 1.0),
    "ease-in": (0.42, 0.0, 1.0, 1.0),
    "ease-out": (0.0, 0.0, 0.58, 1.0),
    "ease-in-out": (0.42, 0.0, 0.58, 1.0),
}


def _cubic_bezier_point(p1, p2, t):
    """Evaluate a unit cubic bezier (P0=(0,0), P3=(1,1)) at parameter t."""
    inv = 1.0 - t
    x = 3 * inv * inv * t * p1[0] + 3 * inv * t * t * p2[0] + t * t * t
    y = 3 * inv * inv * t * p1[1] + 3 * inv * t * t * p2[1] + t * t * t
    return x, y


def _cubic_bezier_y_for_x(p1, p2, x, epsilon=1e-6):
    """Solve for the y value of the bezier curve at a given x (time)."""
    t = x
    for _ in range(32):
        cx, _ = _cubic_bezier_point(p1, p2, t)
        err = cx - x
        if abs(err) < epsilon:
            break
        # Derivative of x with respect to t (Newton-Raphson step).
        inv = 1.0 - t
        dx = (3 * inv * inv * p1[0]
              + 6 * inv * t * (p2[0] - p1[0])
              + 3 * t * t * (1.0 - p2[0]))
        if abs(dx) < epsilon:
            break
        t -= err / dx
        t = min(1.0, max(0.0, t))
    _, y = _cubic_bezier_point(p1, p2, t)
    return min(1.0, max(0.0, y))


class UIAnimationSystem:
    def __init__(self):
        # element_id -> {animation_id: animation-state-dict}
        self.state = {}
        # Cache of sampled curve tables: cache-key -> [eased progress values]
        self._curve_cache = {}
        # container_id -> list of queued transitions
        self._transition_queues = {}
        self._curve_samples = 64

    # ------------------------------------------------------------------
    # Animation curves
    # ------------------------------------------------------------------
    def evaluate_curve(self, easing="linear", control_points=None, t=None,
                       duration=1.0, time_scale=1.0, samples=None):
        """Evaluate an easing curve.

        Supports preset names and custom cubic-bezier control points.
        Time scaling normalizes progress by duration so the curve shape is
        independent of how long the animation runs. Sampled curves are
        cached so repeated evaluations do not recompute the bezier.
        """
        if control_points is not None:
            if len(control_points) != 4:
                raise ValueError("control_points must be (x1, y1, x2, y2)")
            p1 = (float(control_points[0]), float(control_points[1]))
            p2 = (float(control_points[2]), float(control_points[3]))
            curve_key = ("custom", p1, p2)
        else:
            if easing not in EASING_PRESETS:
                raise ValueError(f"unknown easing preset: {easing!r}")
            x1, y1, x2, y2 = EASING_PRESETS[easing]
            p1, p2 = (x1, y1), (x2, y2)
            curve_key = ("preset", easing)

        samples = samples or self._curve_samples
        cache_key = (curve_key, samples)
        table = self._curve_cache.get(cache_key)
        if table is None:
            table = [
                _cubic_bezier_y_for_x(p1, p2, i / (samples - 1))
                for i in range(samples)
            ]
            self._curve_cache[cache_key] = table

        def sample(progress):
            progress = min(1.0, max(0.0, progress))
            pos = progress * (samples - 1)
            low = int(pos)
            high = min(low + 1, samples - 1)
            frac = pos - low
            return table[low] + (table[high] - table[low]) * frac

        if t is None:
            return list(table)

        # Time scaling: normalize absolute time by duration so the curve
        # shape stays the same no matter what duration is configured.
        duration = max(float(duration), 1e-9)
        progress = (float(t) * float(time_scale)) / duration
        return sample(progress)

    # ------------------------------------------------------------------
    # Tween animation
    # ------------------------------------------------------------------
    def tween_animation(self, element_id=None, property="opacity",
                        start=0.0, end=1.0, duration=1.0, easing="ease-in-out",
                        control_points=None, loop=False, yoyo=False,
                        delay=0.0, fps=60, on_complete=None):
        """Build a tween with easing, optional looping, yoyo and delay."""
        element_id = element_id or "default"
        duration = max(float(duration), 1e-9)
        frame_count = max(2, int(round(duration * fps)) + 1)
        frames = []
        for i in range(frame_count):
            t = i / fps
            progress = self.evaluate_curve(
                easing=easing, control_points=control_points,
                t=t, duration=duration,
            )
            frames.append(round(start + (end - start) * progress, 6))

        animation_id = f"tween-{uuid.uuid4().hex[:8]}"
        spec = {
            "id": animation_id,
            "element_id": element_id,
            "type": "tween",
            "property": property,
            "start": start,
            "end": end,
            "duration": duration,
            "easing": easing,
            "control_points": control_points,
            "loop": bool(loop),
            "yoyo": bool(yoyo),
            "delay": max(0.0, float(delay)),
            "frames": frames,
            "on_complete": on_complete,
        }
        self.update_animation_state(element_id, animation_id=animation_id,
                                    action="start", spec=spec)
        return spec

    # ------------------------------------------------------------------
    # Page transition
    # ------------------------------------------------------------------
    def transition_page(self, from_page=None, to_page=None, container_id="root",
                        direction="forward", duration=0.3, easing="ease-in-out",
                        translate=40.0, scale=0.95):
        """Build a page transition with opacity + translate + scale.

        Entering and exiting pages animate differently based on direction.
        Transitions on the same container are queued so rapid page switches
        do not stack overlapping animations.
        """
        sign = 1.0 if direction in ("forward", "right", "down") else -1.0
        spec = {
            "id": f"transition-{uuid.uuid4().hex[:8]}",
            "type": "transition",
            "container_id": container_id,
            "from_page": from_page,
            "to_page": to_page,
            "direction": direction,
            "duration": max(float(duration), 1e-9),
            "easing": easing,
            "enter": {
                "opacity": (0.0, 1.0),
                "translate_x": (sign * translate, 0.0),
                "scale": (scale, 1.0),
            },
            "exit": {
                "opacity": (1.0, 0.0),
                "translate_x": (0.0, -sign * translate),
                "scale": (1.0, scale),
            },
        }

        queue = self._transition_queues.setdefault(container_id, [])
        if queue:
            # A transition is already running: queue this one instead of
            # letting the animations overlap.
            spec["queued"] = True
            spec["queue_position"] = len(queue)
            queue.append(spec)
        else:
            spec["queued"] = False
            spec["queue_position"] = 0
            queue.append(spec)
        return spec

    def _finish_transition(self, container_id):
        """Pop the running transition and start the next queued one."""
        queue = self._transition_queues.get(container_id, [])
        if queue:
            queue.pop(0)
        if queue:
            queue[0]["queued"] = False
            queue[0]["queue_position"] = 0
            return queue[0]
        return None

    # ------------------------------------------------------------------
    # Animation state management
    # ------------------------------------------------------------------
    def update_animation_state(self, element_id=None, animation_id=None,
                               action="start", spec=None, now=None):
        """Manage per-element animation state.

        Multiple animations on the same element are tracked independently
        (keyed by animation id) so they never overwrite each other.
        Supports pause/resume (progress is preserved) and completion
        callbacks.
        """
        element_id = element_id or "default"
        animations = self.state.setdefault(element_id, {})
        now = time.monotonic() if now is None else now

        if action == "start":
            animation_id = animation_id or f"anim-{uuid.uuid4().hex[:8]}"
            animations[animation_id] = {
                "id": animation_id,
                "spec": spec or {},
                "status": "running",
                "started_at": now,
                "elapsed": 0.0,
                "paused_at": None,
            }
            return animations[animation_id]

        if animation_id is None:
            # Apply the action to every animation on the element.
            return {
                aid: self.update_animation_state(element_id, aid, action, now=now)
                for aid in list(animations)
            }

        anim = animations.get(animation_id)
        if anim is None:
            return None

        if action == "pause" and anim["status"] == "running":
            anim["elapsed"] += now - anim["started_at"]
            anim["paused_at"] = now
            anim["status"] = "paused"
        elif action == "resume" and anim["status"] == "paused":
            # Restore position: keep accumulated elapsed time, restart clock.
            anim["started_at"] = now
            anim["paused_at"] = None
            anim["status"] = "running"
        elif action == "stop":
            anim["status"] = "stopped"
        elif action == "finish":
            anim["status"] = "finished"
            callback = (anim.get("spec") or {}).get("on_complete")
            if callable(callback):
                callback(anim)
        return anim

    # ------------------------------------------------------------------
    # Performance optimization
    # ------------------------------------------------------------------
    def optimize_animation(self, spec=None, properties=None, fps=60,
                           will_change=True):
        """Optimize an animation spec for rendering performance.

        - Converts left/top layout properties to hardware-accelerated
          transform (translate3d) so compositing stays on the GPU.
        - Adds will-change hints so the browser can pre-optimize.
        - Throttles frame output to the target fps to avoid triggering
          layout/reflow on every frame.
        """
        spec = dict(spec or {})
        props = list(properties or spec.get("properties")
                     or ([spec["property"]] if "property" in spec
                         else ["opacity", "transform"]))

        transform_parts = []
        optimized_props = []
        for prop in props:
            if prop == "left":
                transform_parts.append("translateX")
            elif prop == "top":
                transform_parts.append("translateY")
            else:
                optimized_props.append(prop)

        optimized = {
            "properties": optimized_props,
            "use_transform": bool(transform_parts),
            "transform": (
                "translate3d(0, 0, 0)" if not transform_parts
                else " ".join(transform_parts) + " translateZ(0)"
            ),
            "will_change": (
                ", ".join(optimized_props + (["transform"] if transform_parts
                                             or "transform" in optimized_props
                                             else []))
                if will_change else None
            ),
            "hardware_accelerated": True,
            "throttle_fps": fps,
            "min_frame_interval": 1.0 / max(1, fps),
        }

        frames = spec.get("frames")
        if frames:
            # Throttle: drop frames that fall inside the min frame interval.
            source_fps = spec.get("fps") or max(1, len(frames) - 1)
            step = max(1, round(source_fps / max(1, fps)))
            throttled = frames[::step]
            if throttled[-1] != frames[-1]:
                throttled.append(frames[-1])
            optimized["frames"] = throttled

        spec["optimized"] = optimized
        return spec
