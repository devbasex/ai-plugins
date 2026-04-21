# テスト実装例

## 例1: 既存関数のユニットテスト

### 元のコード

```javascript
// src/utils/calculator.js
function add(a, b) {
  return a + b;
}

function divide(a, b) {
  if (b === 0) {
    throw new Error('Division by zero');
  }
  return a / b;
}

module.exports = { add, divide };
```

### 生成されるテスト

```javascript
// tests/utils/calculator.test.js
const { add, divide } = require('../../src/utils/calculator');

describe('calculator', () => {
  describe('add', () => {
    test('should add two positive numbers', () => {
      // Arrange
      const a = 2;
      const b = 3;
      const expected = 5;

      // Act
      const result = add(a, b);

      // Assert
      expect(result).toBe(expected);
    });

    test('should handle negative numbers', () => {
      expect(add(-2, -3)).toBe(-5);
    });

    test('should handle zero', () => {
      expect(add(0, 5)).toBe(5);
      expect(add(5, 0)).toBe(5);
    });
  });

  describe('divide', () => {
    test('should divide two numbers', () => {
      expect(divide(10, 2)).toBe(5);
    });

    test('should throw error when dividing by zero', () => {
      expect(() => divide(10, 0)).toThrow('Division by zero');
    });

    test('should handle negative numbers', () => {
      expect(divide(-10, 2)).toBe(-5);
    });
  });
});
```

## 例2: API統合テスト

```javascript
// tests/api/users.integration.test.js
const request = require('supertest');
const app = require('../../app');
const db = require('../../models');

describe('Users API', () => {
  beforeAll(async () => {
    await db.sequelize.sync({ force: true });
  });

  afterAll(async () => {
    await db.sequelize.close();
  });

  describe('GET /api/users', () => {
    test('should return all users', async () => {
      const response = await request(app)
        .get('/api/users')
        .expect('Content-Type', /json/)
        .expect(200);

      expect(response.body.success).toBe(true);
      expect(Array.isArray(response.body.data)).toBe(true);
    });

    test('should support pagination', async () => {
      const response = await request(app)
        .get('/api/users?page=1&limit=10')
        .expect(200);

      expect(response.body.pagination).toBeDefined();
      expect(response.body.pagination.page).toBe(1);
      expect(response.body.pagination.limit).toBe(10);
    });
  });

  describe('POST /api/users', () => {
    test('should create new user', async () => {
      const newUser = {
        name: 'Test User',
        email: 'test@example.com'
      };

      const response = await request(app)
        .post('/api/users')
        .send(newUser)
        .expect(201);

      expect(response.body.success).toBe(true);
      expect(response.body.data.name).toBe(newUser.name);
    });

    test('should return 400 for invalid data', async () => {
      const invalidUser = {
        name: 'A', // Too short
        email: 'invalid-email'
      };

      const response = await request(app)
        .post('/api/users')
        .send(invalidUser)
        .expect(400);

      expect(response.body.success).toBe(false);
      expect(response.body.errors).toBeDefined();
    });
  });
});
```

## 例3: pytest (Python)

```python
# tests/test_calculator.py
import pytest
from src.calculator import add, divide

class TestAdd:
    def test_add_positive_numbers(self):
        assert add(2, 3) == 5

    def test_add_negative_numbers(self):
        assert add(-2, -3) == -5

    def test_add_zero(self):
        assert add(0, 5) == 5

class TestDivide:
    def test_divide_numbers(self):
        assert divide(10, 2) == 5

    def test_divide_by_zero(self):
        with pytest.raises(ValueError, match="Division by zero"):
            divide(10, 0)

    @pytest.mark.parametrize("a,b,expected", [
        (10, 2, 5),
        (9, 3, 3),
        (-10, 2, -5),
    ])
    def test_divide_parametrized(self, a, b, expected):
        assert divide(a, b) == expected
```

## 例4: Reactコンポーネントテスト

```javascript
// tests/components/UserList.test.jsx
import { render, screen, waitFor } from '@testing-library/react';
import UserList from '../../src/components/UserList';

// APIをモック
jest.mock('../../src/api', () => ({
  fetchUsers: jest.fn()
}));

import { fetchUsers } from '../../src/api';

describe('UserList', () => {
  test('should show loading state', () => {
    fetchUsers.mockImplementation(() => new Promise(() => {}));

    render(<UserList />);

    expect(screen.getByText('Loading...')).toBeInTheDocument();
  });

  test('should display users', async () => {
    fetchUsers.mockResolvedValue([
      { id: 1, name: 'User 1' },
      { id: 2, name: 'User 2' }
    ]);

    render(<UserList />);

    await waitFor(() => {
      expect(screen.getByText('User 1')).toBeInTheDocument();
      expect(screen.getByText('User 2')).toBeInTheDocument();
    });
  });

  test('should show error message', async () => {
    fetchUsers.mockRejectedValue(new Error('Network error'));

    render(<UserList />);

    await waitFor(() => {
      expect(screen.getByText('Error: Network error')).toBeInTheDocument();
    });
  });
});
```
