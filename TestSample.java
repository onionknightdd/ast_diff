package com.example.test;

import java.util.ArrayList;
import java.util.List;

public class UserService {
    private List<User> users;

    public UserService() {
        this.users = new ArrayList<>();
    }

    public boolean addUser(String name, String email) {
        if (email == null || !email.contains("@")) {
            return false;
        }
        User user = new User(name, email);
        user.setActive(true);
        users.add(user);
        return true;
    }

    public User getUser(String email) {
        for (User user : users) {
            if (user.getEmail().equals(email)) {
                return user;
            }
        }
        return null;
    }

    private static class User {
        private String name;
        private String email;

        public User(String name, String email) {
            this.name = name;
            this.email = email;
        }

        public String getEmail() {
            return email;
        }
    }
}
