# テストパターン詳細

## 1. AAA（Arrange-Act-Assert）パターン

```javascript
test('should do something', () => {
  // Arrange: テストデータを準備
  const input = { ... };
  const expected = { ... };

  // Act: テスト対象を実行
  const result = functionUnderTest(input);

  // Assert: 結果を検証
  expect(result).toEqual(expected);
});
```

## 2. エッジケーステスト

```javascript
describe('edge cases', () => {
  test('should handle null input', () => {
    expect(() => func(null)).toThrow();
  });

  test('should handle empty array', () => {
    expect(func([])).toEqual([]);
  });

  test('should handle boundary values', () => {
    expect(func(0)).toBe(...);
    expect(func(-1)).toBe(...);
    expect(func(Number.MAX_VALUE)).toBe(...);
  });
});
```

### テストすべきエッジケース

- **null / undefined**: 入力がnull/undefinedの場合
- **空値**: 空文字列、空配列、空オブジェクト
- **境界値**: 0, -1, MAX_VALUE, MIN_VALUE
- **型エラー**: 文字列が期待される場所に数値
- **特殊文字**: Unicode、改行、タブ

## 3. モック・スパイ

### モックの作成

```javascript
test('should call API', async () => {
  // モック作成
  const mockFetch = jest.fn().mockResolvedValue({
    json: () => ({ data: 'test' })
  });
  global.fetch = mockFetch;

  // 実行
  await fetchData();

  // 検証
  expect(mockFetch).toHaveBeenCalledWith('/api/data');
  expect(mockFetch).toHaveBeenCalledTimes(1);
});
```

### スパイの使用

```javascript
test('should log error', () => {
  const consoleSpy = jest.spyOn(console, 'error').mockImplementation();

  causeError();

  expect(consoleSpy).toHaveBeenCalledWith('Error occurred');
  consoleSpy.mockRestore();
});
```

### モジュールモック

```javascript
jest.mock('./database', () => ({
  query: jest.fn().mockResolvedValue([{ id: 1 }])
}));

const { query } = require('./database');

test('should fetch from database', async () => {
  const result = await getUserById(1);
  expect(query).toHaveBeenCalledWith('SELECT * FROM users WHERE id = ?', [1]);
});
```

## 4. テストフィクスチャ

### beforeEach / afterEach

```javascript
describe('UserService', () => {
  let userService;
  let mockDb;

  beforeEach(() => {
    mockDb = createMockDatabase();
    userService = new UserService(mockDb);
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  test('should create user', () => {
    // テスト
  });
});
```

### テストデータファクトリ

```javascript
// factories/user.js
function createUser(overrides = {}) {
  return {
    id: 1,
    name: 'Test User',
    email: 'test@example.com',
    ...overrides
  };
}

// テストで使用
test('should update user', () => {
  const user = createUser({ name: 'Updated Name' });
  // ...
});
```

## 5. 非同期テスト

### async/await

```javascript
test('should fetch data', async () => {
  const data = await fetchData();
  expect(data).toEqual({ success: true });
});
```

### Promise

```javascript
test('should resolve with data', () => {
  return fetchData().then(data => {
    expect(data).toEqual({ success: true });
  });
});
```

### タイムアウト

```javascript
test('should complete within timeout', async () => {
  await expect(slowOperation()).resolves.toBe('done');
}, 10000); // 10秒タイムアウト
```
