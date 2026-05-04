"""Tests for users API endpoints."""
import pytest
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api.main import app, users_db


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_users():
    """Clear users database before each test."""
    users_db.clear()
    yield
    users_db.clear()


class TestGetUsers:
    """Tests for GET /users endpoint."""

    def test_get_users_empty(self, client):
        """Test that GET /users returns empty list initially."""
        response = client.get("/users")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_users_with_data(self, client):
        """Test that GET /users returns created users."""
        # Create a user first
        client.post("/users", json={"name": "Alice", "email": "alice@example.com"})
        
        response = client.get("/users")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Alice"
        assert data[0]["email"] == "alice@example.com"


class TestPostUsers:
    """Tests for POST /users endpoint."""

    def test_create_user(self, client):
        """Test creating a new user."""
        response = client.post("/users", json={
            "name": "John Doe",
            "email": "john@example.com"
        })
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "John Doe"
        assert data["email"] == "john@example.com"
        assert "id" in data

    def test_create_user_with_missing_fields(self, client):
        """Test that creating user without required fields fails."""
        response = client.post("/users", json={"name": "John"})
        assert response.status_code == 422  # FastAPI validation error

    def test_create_user_id_increments(self, client):
        """Test that user IDs increment."""
        response1 = client.post("/users", json={"name": "User1", "email": "user1@example.com"})
        response2 = client.post("/users", json={"name": "User2", "email": "user2@example.com"})
        
        assert response1.json()["id"] == 1
        assert response2.json()["id"] == 2