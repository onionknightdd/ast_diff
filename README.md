# AST Diff 分析器

精确分析 Git diff 中的代码改动，支持 **Java** 和 **Python**，使用 AST（抽象语法树）进行精确定位。

## ✨ 特性

- 🎯 **精确定位**: 使用 AST 精确识别改动所属的类、方法、函数
- 🔄 **多语言支持**: 同时支持 Java 和 Python
- 📊 **统计分析**: 显示改动最多的结构
- 🎨 **彩色输出**: 使用颜色高亮关键信息
- 🔍 **详细模式**: 可选的详细信息展示

## 📦 安装

### 1. 安装 Python 依赖

```bash
# 安装所有依赖
pip install javalang colorama

# 或者只安装 Python 支持（不需要 javalang）
pip install colorama
```

### 2. 下载脚本

```bash
# 下载并添加执行权限
chmod +x ast_code_diff.py
```

## 🚀 使用方法

### 基本用法

```bash
# 分析当前工作区的改动
python ast_code_diff.py

# 比较两个提交
python ast_code_diff.py HEAD~1 HEAD

# 比较两个分支
python ast_code_diff.py main feature-branch

# 详细模式（显示更多改动）
python ast_code_diff.py -v

# 显示统计信息
python ast_code_diff.py -s

# 组合使用
python ast_code_diff.py -v -s HEAD~5 HEAD
```

### 指定仓库路径

```bash
# 分析其他仓库
python ast_code_diff.py --repo /path/to/your/repo

# 分析其他仓库的特定提交
python ast_code_diff.py --repo /path/to/repo HEAD~1 HEAD
```

### 命令行选项

```
positional arguments:
  commit1               第一个提交/分支
  commit2               第二个提交/分支

optional arguments:
  -h, --help            显示帮助信息
  -v, --verbose         详细模式，显示更多改动
  -s, --stats           显示统计信息
  --repo REPO           指定 Git 仓库路径（默认: 当前目录）
```

## 📖 输出示例

### Python 代码分析

```
================================================================================
AST Diff 分析结果
================================================================================

📊 总计: 2 个文件, 15 行改动

📄 src/service.py
────────────────────────────────────────────────────────────────────────────

  🔹 class UserService > @staticmethod method get_user
     (3 行改动)
     L  45: def get_user(user_id: int) -> Optional[User]:
     L  46:     # 添加缓存逻辑
     L  47:     cached = cache.get(f"user:{user_id}")

  🔹 class UserService > method validate_email
     (2 行改动)
     L  78:     if not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email):
     L  79:         raise ValueError("Invalid email format")
```

### Java 代码分析

```
📄 src/main/java/com/example/UserController.java
────────────────────────────────────────────────────────────────────────────

  🔹 public class UserController > public method createUser
     (5 行改动)
     L  32: @PostMapping("/users")
     L  33: public ResponseEntity<User> createUser(@RequestBody User user) {
     L  34:     // 添加参数校验
     L  35:     validator.validate(user);
     L  36:     return ResponseEntity.ok(userService.create(user));

  🔹 public static class UserController > private method validateRequest
     (3 行改动)
     L  65:     if (request == null) {
     L  66:         throw new IllegalArgumentException("Request cannot be null");
     L  67:     }
```

### 统计信息

```
================================================================================
统计信息
================================================================================

🏆 改动最多的结构 (Top 10):

   1.  15 行 | public class UserService > public method processUser
   2.   8 行 | class DataProcessor > method transform
   3.   5 行 | public class UserController > private method validate
   4.   3 行 | class Utils > @staticmethod function format_date
```

## 🔧 技术细节

### Python 分析

使用 Python 内置的 `ast` 模块：

- ✅ 类定义 (ClassDef)
- ✅ 函数定义 (FunctionDef)
- ✅ 方法定义（类中的函数）
- ✅ 异步函数 (AsyncFunctionDef)
- ✅ 装饰器识别
- ✅ 类型注解提取
- ✅ 嵌套结构支持

### Java 分析

使用 `javalang` 库进行 AST 解析：

- ✅ 类声明 (ClassDeclaration)
- ✅ 接口声明 (InterfaceDeclaration)
- ✅ 枚举声明 (EnumDeclaration)
- ✅ 方法声明 (MethodDeclaration)
- ✅ 构造函数 (ConstructorDeclaration)
- ✅ 内部类支持
- ✅ 访问修饰符识别
- ✅ 泛型支持

### 行号映射策略

1. **直接映射**: 每个结构的每一行都建立映射
2. **最小范围优先**: 当一行属于多个结构时，选择范围最小的（最具体的）
3. **大括号匹配**: Java 通过大括号匹配确定结构边界
4. **AST 节点范围**: Python 使用 `lineno` 和 `end_lineno` 精确定位

## 🐛 故障排除

### 问题：Java 分析不工作

```bash
# 确保安装了 javalang
pip install javalang

# 验证安装
python -c "import javalang; print('OK')"
```

### 问题：颜色输出异常

```bash
# 安装 colorama（Windows 必需）
pip install colorama

# 或者禁用颜色（通过重定向）
./ast_diff_analyzer.py > output.txt
```

### 问题：找不到文件

确保在 Git 仓库中运行，或使用 `--repo` 参数指定路径：

```bash
python ast_code_diff.py --repo /path/to/your/repo
```

### 问题：语法错误

如果代码有语法错误，该文件会被跳过，但不影响其他文件的分析。

## 🎯 应用场景

### 1. Code Review

```bash
# 查看 PR 改动影响了哪些函数
python ast_code_diff.py origin/main HEAD

# 查看改动最多的部分
python ast_code_diff.py -s origin/main HEAD
```

### 2. 重构分析

```bash
# 详细查看重构前后的变化
python ast_code_diff.py -v before-refactor after-refactor
```

### 3. 发布前检查

```bash
# 查看即将发布的改动
python ast_code_diff.py -s v1.0.0 HEAD
```

### 4. Bug 定位

```bash
# 查看最近改动了哪些函数
python ast_code_diff.py HEAD~10 HEAD
```

## 🔄 与其他工具集成

### Git Alias

添加到 `~/.gitconfig`:

```ini
[alias]
    diff-ast = "!python /path/to/ast_code_diff.py"
    diff-stats = "!python /path/to/ast_code_diff.py -s"
```

使用:

```bash
git diff-ast HEAD~1 HEAD
git diff-stats main feature-branch
```

### Pre-commit Hook

创建 `.git/hooks/pre-commit`:

```bash
#!/bin/bash
python /path/to/ast_code_diff.py
```

### CI/CD 集成

在 GitHub Actions 或 GitLab CI 中：

```yaml
- name: Analyze code changes
  run: |
    python ast_code_diff.py origin/main HEAD > diff_analysis.txt
    cat diff_analysis.txt
```

## 📝 示例项目

查看 `examples/` 目录获取完整的测试示例。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可

MIT License

## 🔗 相关资源

- [Python AST 文档](https://docs.python.org/3/library/ast.html)
- [javalang GitHub](https://github.com/c2nes/javalang)
- [Git Diff 格式说明](https://git-scm.com/docs/git-diff)