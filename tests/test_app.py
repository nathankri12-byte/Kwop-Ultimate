import unittest
from unittest.mock import patch
import app as application

class AppSmokeTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client=application.app.test_client()

    def test_health_is_public(self):
        response=self.client.get("/api/health")
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()["status"],"ok")

    def test_private_workspace_requires_sign_in(self):
        response=self.client.get("/api/leads")
        self.assertEqual(response.status_code,401)

    def test_login_requires_supabase_configuration(self):
        with patch.object(application,"SB_KEY",""):
            response=self.client.post("/api/auth/login",json={"email":"test@example.com","password":"password"})
        self.assertEqual(response.status_code,503)

    def test_public_event_rejects_invalid_type(self):
        with patch.object(application,"SB_KEY","publishable-test-key"):
            response=self.client.post("/api/public-event",json={"event_type":"arbitrary","launch_id":"00000000-0000-0000-0000-000000000000","session_key":"session-12345678"})
        self.assertEqual(response.status_code,400)

    def test_public_page_escapes_user_content_and_shows_consent(self):
        sample={"id":"00000000-0000-0000-0000-000000000000","slug":"demo","title":"<script>alert(1)</script>","description":"Description","landing_copy":{"headline":"<img src=x onerror=alert(1)>","subheadline":"Safe subtitle","body":"Safe body"},"plans":[]}
        with patch.object(application,"select",return_value=[sample]):
            response=self.client.get("/p/demo")
        content=response.get_data(as_text=True)
        self.assertEqual(response.status_code,200)
        self.assertIn("Accept analytics",content)
        self.assertNotIn("<script>alert(1)</script>",content)
        self.assertIn("&lt;script&gt;",content)

if __name__=="__main__":
    unittest.main()
