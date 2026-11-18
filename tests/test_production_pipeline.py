<<<<<<< HEAD
import unittest
from fastapi.testclient import TestClient
from app.main import app


class ProductionAppTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_and_projects_endpoints(self):
        r_health = self.client.get("/health")
        self.assertEqual(r_health.status_code, 200)
        self.assertEqual(r_health.json()["status"], "ok")

        r_projects = self.client.get("/api/projects")
        self.assertEqual(r_projects.status_code, 200)
        data = r_projects.json()
        self.assertIn("busy", data)
        self.assertIn("projects", data)

    def test_path_traversal_rejected(self):
        res = self.client.get("/api/projects/..%2F..%2Fetc/script")
        self.assertIn(res.status_code, (400, 404))


if __name__ == "__main__":
    unittest.main()
=======
test pipeline
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
