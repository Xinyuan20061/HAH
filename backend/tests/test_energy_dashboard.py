def test_energy_dashboard_groups_meals_and_adjusts_today_threshold(api):
    profile = api.put(
        "/api/v1/users/me/health-profile",
        json={
            "gender": "male",
            "age": 30,
            "height_cm": 180,
            "weight_kg": 80,
            "goal_type": "maintain",
            "activity_level": "moderate",
        },
    )
    assert profile.status_code == 200
    assert api.post(
        "/api/v1/diet/records",
        json={"name": "燕麦早餐", "meal_type": "breakfast", "calories": 400},
    ).status_code == 200
    assert api.post(
        "/api/v1/diet/records",
        json={"name": "午餐", "meal_type": "lunch", "calories": 600},
    ).status_code == 200
    assert api.post(
        "/api/v1/exercise/records",
        json={"name": "跑步", "duration_min": 30, "calories_burned": 300},
    ).status_code == 200

    response = api.get("/api/v1/health/energy-dashboard")
    assert response.status_code == 200
    data = response.json()
    today = data["today"]
    assert len(data["days"]) == 7
    assert today["breakfast"] == 400
    assert today["lunch"] == 600
    assert today["intake"] == 1000
    assert today["exercise"] == 300
    assert data["target"]["base"] == 2000
    assert data["target"]["today"] == 2075
    assert data["resting"]["calories"] == 1780
    assert today["net"] == -1080
    assert "漏记" in data["target"]["note"]


def test_energy_dashboard_does_not_invent_resting_energy_without_profile(api):
    response = api.get("/api/v1/health/energy-dashboard")
    assert response.status_code == 200
    data = response.json()
    assert data["resting"]["available"] is False
    assert data["resting"]["calories"] is None
    assert data["today"]["net"] is None
    assert data["today"]["balance"] == "complete_profile"
