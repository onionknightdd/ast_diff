#!/usr/bin/env python3
"""Sample Python file for testing AST diff analyzer."""

class UserService:
    """Service for managing users."""

    def __init__(self):
        self.users = []

    def add_user(self, name: str, email: str) -> bool:
        """Add a new user."""
        if not email or "@" not in email:
            return False
        user = {"name": name, "email": email, "active": True}
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
    if not items:
        return 0.0
    total = 0.0
    for item in items:
        price = item.get("price", 0.0)
        discount = item.get("discount", 0.0)
        total += price * (1 - discount)
    return round(total, 2)


def main():
    """Main entry point."""
    service = UserService()
    result = service.add_user("Alice", "alice@example.com")
    if result:
        print("User added successfully")
    else:
        print("Failed to add user")


if __name__ == "__main__":
    main()
