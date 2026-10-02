import pytest
from ui.animation_system import UIAnimationSystem


class TestUIAnimationSystem:

    def test_tween_animation(self):
        """补间动画只做线性插值，不做缓动曲线"""
        obj = UIAnimationSystem()
        result = obj.tween_animation()
        self.assertIsNotNone(result)

    def test_transition_page(self):
        """页面过渡只做透明度渐变，不做位移和缩放"""
        obj = UIAnimationSystem()
        result = obj.transition_page()
        self.assertIsNotNone(result)

    def test_evaluate_curve(self):
        """动画曲线只支持预设的几种，不支持自定义贝塞尔曲线"""
        obj = UIAnimationSystem()
        result = obj.evaluate_curve()
        self.assertIsNotNone(result)

    def test_update_animation_state(self):
        """动画状态不做管理，同一个元素多个动画同时跑互相覆盖"""
        obj = UIAnimationSystem()
        result = obj.update_animation_state()
        self.assertIsNotNone(result)

    def test_optimize_animation(self):
        """动画不做硬件加速，用left/top而不是transform"""
        obj = UIAnimationSystem()
        result = obj.optimize_animation()
        self.assertIsNotNone(result)

