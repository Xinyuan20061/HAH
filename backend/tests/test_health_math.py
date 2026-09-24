from app.services.health import calorie_target


class P:
    goal_type = "lose"


def test_calorie_target_lose():
    assert calorie_target(P()) == 1700
