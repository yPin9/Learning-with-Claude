def test_create_task(client):
    resp = client.post(
        "/tasks",
        json={"title": "learn docker", "milestone": "week-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "learn docker"
    assert data["milestone"] == "week-1"
    assert data["done"] is False
    assert isinstance(data["id"], int)


def test_list_tasks(client):
    client.post("/tasks", json={"title": "a"})
    client.post("/tasks", json={"title": "b"})
    resp = client.get("/tasks")
    assert resp.status_code == 200
    titles = [t["title"] for t in resp.json()]
    assert titles == ["a", "b"]


def test_complete_task(client):
    created = client.post("/tasks", json={"title": "done me"}).json()
    resp = client.patch(f"/tasks/{created['id']}/complete")
    assert resp.status_code == 200
    assert resp.json()["done"] is True


def test_complete_missing_task(client):
    resp = client.patch("/tasks/9999/complete")
    assert resp.status_code == 404
