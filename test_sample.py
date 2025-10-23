#!/usr/bin/env python3
"""Sample Python file for testing AST diff analyzer."""

class UserService:
    """Service for managing users."""

    def __init__(self):
        self.users = []

    def add_user(self, name: str, email: str) -> bool:
        """Add a new user."""
        user = {"name": name, "email": email}
        self.users.append(user)
        return True

    def get_user(self, email: str):
        """Get user by email."""
        for user in self.users:
            if user["email"] == email:
                return user
        return None


def calculate_total(items: list) -> float:
    """Calculate total price of items."""
    total = 0.0
    for item in items:
        total += item.get("price", 0.0)
    return total


def main():
    """Main entry point."""
    service = UserService()
    service.add_user("Alice", "alice@example.com")
    print("User added successfully")


if __name__ == "__main__":
    main()
