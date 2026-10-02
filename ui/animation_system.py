"""
pairwise-ui-animation-system - UIAnimationSystem

This module contains a deliberately broken implementation.
Fix all bugs so that tests pass.
"""


class UIAnimationSystem:
    def __init__(self):
        self.state = {}

    def tween_animation(self, *args, **kwargs):
        """BUG: 补间动画只做线性插值，不做缓动曲线"""
        return None

    def transition_page(self, *args, **kwargs):
        """BUG: 页面过渡只做透明度渐变，不做位移和缩放"""
        return None

    def evaluate_curve(self, *args, **kwargs):
        """BUG: 动画曲线只支持预设的几种，不支持自定义贝塞尔曲线"""
        return None

    def update_animation_state(self, *args, **kwargs):
        """BUG: 动画状态不做管理，同一个元素多个动画同时跑互相覆盖"""
        return None

    def optimize_animation(self, *args, **kwargs):
        """BUG: 动画不做硬件加速，用left/top而不是transform"""
        return None

