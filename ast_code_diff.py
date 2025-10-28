"""
AST Diff Analyzer - Precisely analyze Java and Python code changes.

This tool uses Abstract Syntax Trees to accurately identify which classes,
functions, and methods were modified in Git diffs.

Dependencies:
    pip install javalang colorama
"""

import ast
import logging
import re
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Optional dependencies
try:
    import javalang
    JAVALANG_AVAILABLE = True
except ImportError:
    JAVALANG_AVAILABLE = False

try:
    from colorama import Fore, Style, init
    init(autoreset=True)
    COLORAMA_AVAILABLE = True
except ImportError:
    COLORAMA_AVAILABLE = False
    # Define empty color classes for graceful degradation
    class _DummyColor:
        def __getattr__(self, item: str) -> str:
            return ''
    Fore = _DummyColor()
    Style = _DummyColor()


# ============================================================================
# Constants
# ============================================================================

class StructureType(str, Enum):
    """Code structure types."""
    CLASS = 'class'
    INTERFACE = 'interface'
    ENUM = 'enum'
    FUNCTION = 'function'
    METHOD = 'method'
    CONSTRUCTOR = 'constructor'


# Display constants
DEFAULT_MAX_LINES_DISPLAY = 5
VERBOSE_MAX_LINES_DISPLAY = 10
MAX_LINE_LENGTH = 70
TOP_STRUCTURES_LIMIT = 10
SEPARATOR_WIDTH = 80
MIN_GAP_FOR_ELLIPSIS = 5  # Minimum line gap to show ...

# Java analyzer estimation constants
DEFAULT_CLASS_LINES = 1000
DEFAULT_INTERFACE_LINES = 1000
DEFAULT_ENUM_LINES = 100
DEFAULT_METHOD_LINES = 50
DEFAULT_CONSTRUCTOR_LINES = 50
FALLBACK_END_LINE_ESTIMATE = 100

# Diff parsing patterns
DIFF_FILE_PREFIX = 'b/'
DIFF_HUNK_PATTERN = r'@@ -\d+,?\d* \+(\d+),?\d* @@'


# ============================================================================
# Custom Exceptions
# ============================================================================

class ASTDiffError(Exception):
    """Base exception for AST Diff errors."""
    pass


class ParseError(ASTDiffError):
    """Exception raised when code parsing fails."""
    pass


class GitError(ASTDiffError):
    """Exception raised when git operations fail."""
    pass


# ============================================================================
# Logging Configuration
# ============================================================================

def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure logging for the application.

    Args:
        verbose: Enable debug logging if True.

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger('ast_diff')
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG if verbose else logging.INFO)

    formatter = logging.Formatter('%(levelname)s: %(message)s')
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    return logger


logger = setup_logging()


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class CodeStructure:
    """Represents a code structure (class, function, method, etc.).

    Attributes:
        name: The name of the structure.
        type: The type of structure (class, function, method, etc.).
        start_line: Starting line number (1-indexed).
        end_line: Ending line number (1-indexed).
        parent: Parent structure if nested.
        modifiers: Access modifiers and decorators.
        params: Parameter list for functions/methods.
        return_type: Return type annotation.
    """
    name: str
    type: StructureType
    start_line: int
    end_line: int
    parent: Optional['CodeStructure'] = None
    modifiers: List[str] = field(default_factory=list)
    params: List[str] = field(default_factory=list)
    return_type: str = ''

    def get_full_path(self) -> str:
        """Get the full hierarchical path of this structure.

        Returns:
            String representation like "class Foo > method bar".
        """
        parts = []
        current = self
        while current:
            modifier_str = ' '.join(current.modifiers) + ' ' if current.modifiers else ''
            parts.append(f"{modifier_str}{current.type.value} {current.name}")
            current = current.parent
        return ' > '.join(reversed(parts))

    def get_signature(self) -> str:
        """Get the signature for callable structures.

        Returns:
            Function/method signature with parameters and return type.
        """
        if self.type in {StructureType.FUNCTION, StructureType.METHOD, StructureType.CONSTRUCTOR}:
            params_str = ', '.join(self.params) if self.params else ''
            return_str = f" -> {self.return_type}" if self.return_type else ''
            return f"{self.name}({params_str}){return_str}"
        return self.name

    @property
    def line_count(self) -> int:
        """Get the number of lines in this structure."""
        return self.end_line - self.start_line + 1

    def to_dict(self) -> dict:
        """Convert to dictionary format for JSON serialization.

        Returns:
            Dictionary representation of the structure.
        """
        return {
            'name': self.name,
            'type': self.type.value,
            'start_line': self.start_line,
            'end_line': self.end_line,
            'full_path': self.get_full_path(),
            'signature': self.get_signature(),
            'modifiers': self.modifiers,
            'params': self.params,
            'return_type': self.return_type,
            'line_count': self.line_count
        }


@dataclass
class DiffChange:
    """Represents a single line change in a diff.

    Attributes:
        file_path: Path to the changed file.
        line_num: Line number in the file.
        content: Content of the changed line.
        structure: Code structure containing this change.
        change_type: Type of change ('+' for addition, '-' for deletion).
    """
    file_path: str
    line_num: int
    content: str
    structure: Optional[CodeStructure] = None
    change_type: str = '+'  # '+' for addition, '-' for deletion

    def to_dict(self) -> dict:
        """Convert to dictionary format for JSON serialization.

        Returns:
            Dictionary representation of the change.
        """
        return {
            'file_path': self.file_path,
            'line_num': self.line_num,
            'content': self.content,
            'change_type': self.change_type,
            'structure': self.structure.to_dict() if self.structure else None
        }


# ============================================================================
# Language Analyzers
# ============================================================================

class LanguageAnalyzer(ABC):
    """Abstract base class for language-specific code analyzers.

    Attributes:
        file_path: Path to the source file.
        content: Source code content.
        structures: List of identified code structures.
        line_map: Mapping from line numbers to structures.
    """

    def __init__(self, file_path: str, content: str):
        """Initialize the analyzer.

        Args:
            file_path: Path to the source file.
            content: Source code content.
        """
        self.file_path = file_path
        self.content = content
        self.structures: List[CodeStructure] = []
        self.line_map: Dict[int, CodeStructure] = {}

    @abstractmethod
    def parse(self) -> bool:
        """Parse the source code and extract structures.

        Returns:
            True if parsing succeeded, False otherwise.
        """
        pass

    def get_structure_at_line(self, line_num: int) -> Optional[CodeStructure]:
        """Get the most specific structure containing the given line.

        Args:
            line_num: Line number to query (1-indexed).

        Returns:
            The most specific CodeStructure containing this line, or None.
        """
        # Fast path: direct lookup
        if line_num in self.line_map:
            return self.line_map[line_num]

        # Slow path: find the smallest containing structure
        candidates = [
            s for s in self.structures
            if s.start_line <= line_num <= s.end_line
        ]

        if candidates:
            # Return the smallest range (most specific)
            return min(candidates, key=lambda s: s.line_count)

        return None

    def build_line_map(self) -> None:
        """Build a mapping from line numbers to structures.

        Prioritizes smaller (more specific) structures when lines belong
        to multiple nested structures.
        """
        for struct in self.structures:
            for line in range(struct.start_line, struct.end_line + 1):
                # Keep the most specific (smallest range) structure
                if line not in self.line_map:
                    self.line_map[line] = struct
                else:
                    existing = self.line_map[line]
                    if struct.line_count < existing.line_count:
                        self.line_map[line] = struct


class PythonAnalyzer(LanguageAnalyzer):
    """Python code analyzer using the built-in AST module."""

    def parse(self) -> bool:
        """Parse Python code and extract structures.

        Returns:
            True if parsing succeeded, False otherwise.
        """
        try:
            tree = ast.parse(self.content)
            self._visit_node(tree, None)
            self.build_line_map()
            return True
        except SyntaxError as e:
            logger.warning(f"Python syntax error in {self.file_path}: {e}")
            return False

    def _visit_node(self, node: ast.AST, parent: Optional[CodeStructure]) -> None:
        """Recursively visit AST nodes and extract structures.

        Args:
            node: AST node to visit.
            parent: Parent CodeStructure for nested structures.
        """
        if isinstance(node, ast.ClassDef):
            struct = CodeStructure(
                name=node.name,
                type=StructureType.CLASS,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                parent=parent,
                modifiers=self._get_decorators(node)
            )
            self.structures.append(struct)

            # Recursively process methods in the class
            for child in node.body:
                self._visit_node(child, struct)

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_type = (StructureType.METHOD if parent and parent.type == StructureType.CLASS
                        else StructureType.FUNCTION)

            # Extract parameters
            params = [arg.arg for arg in node.args.args] if node.args.args else []

            # Extract return type annotation
            return_type = ast.unparse(node.returns) if node.returns else ''

            struct = CodeStructure(
                name=node.name,
                type=func_type,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                parent=parent,
                modifiers=self._get_decorators(node),
                params=params,
                return_type=return_type
            )
            self.structures.append(struct)

            # Recursively process nested functions
            for child in node.body:
                self._visit_node(child, struct)

        elif hasattr(node, 'body'):
            # Process other nodes with body attributes
            for child in node.body:
                self._visit_node(child, parent)

    def _get_decorators(self, node: ast.AST) -> List[str]:
        """Extract decorator names from a node.

        Args:
            node: AST node with potential decorators.

        Returns:
            List of decorator names with @ prefix.
        """
        decorators = []
        if hasattr(node, 'decorator_list'):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Name):
                    decorators.append(f"@{dec.id}")
                elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
                    decorators.append(f"@{dec.func.id}")
        return decorators


class JavaAnalyzer(LanguageAnalyzer):
    """Java code analyzer using the javalang library."""

    def __init__(self, file_path: str, content: str):
        """Initialize Java analyzer.

        Args:
            file_path: Path to the Java source file.
            content: Java source code content.
        """
        super().__init__(file_path, content)
        self.package_name: str = ''

    def parse(self) -> bool:
        """Parse Java code and extract structures.

        Returns:
            True if parsing succeeded, False otherwise.
        """
        if not JAVALANG_AVAILABLE:
            logger.warning("javalang is not installed, Java analysis unavailable")
            return False

        try:
            tree = javalang.parse.parse(self.content)
            self.package_name = tree.package.name if tree.package else ''

            # Process different declaration types
            for path, node in tree.filter(javalang.tree.ClassDeclaration):
                self._process_class(node, path)

            for path, node in tree.filter(javalang.tree.InterfaceDeclaration):
                self._process_interface(node, path)

            for path, node in tree.filter(javalang.tree.EnumDeclaration):
                self._process_enum(node, path)

            self.build_line_map()
            return True

        except javalang.parser.JavaSyntaxError as e:
            logger.warning(f"Java syntax error in {self.file_path}: {e}")
            return False

    def _process_class(self, node: javalang.tree.ClassDeclaration,
                       path: List) -> None:
        """Process a Java class declaration.

        Args:
            node: ClassDeclaration node from javalang.
            path: Path to this node in the AST.
        """
        if not hasattr(node, 'position') or not node.position:
            return

        # Find parent class if this is an inner class
        parent_class = self._find_parent_structure(path, node, StructureType.CLASS)

        modifiers = list(node.modifiers) if node.modifiers else []

        class_struct = CodeStructure(
            name=node.name,
            type=StructureType.CLASS,
            start_line=node.position.line,
            end_line=node.position.line + DEFAULT_CLASS_LINES,  # Temporary, updated later
            parent=parent_class,
            modifiers=modifiers
        )
        self.structures.append(class_struct)

        # Process constructors
        for constructor in node.constructors:
            self._process_constructor(constructor, class_struct)

        # Process methods
        for method in node.methods:
            self._process_method(method, class_struct)

        # Update end line based on brace matching
        self._update_structure_end_line(class_struct)

    def _process_interface(self, node: javalang.tree.InterfaceDeclaration,
                          path: List) -> None:
        """Process a Java interface declaration.

        Args:
            node: InterfaceDeclaration node from javalang.
            path: Path to this node in the AST.
        """
        if not hasattr(node, 'position') or not node.position:
            return

        modifiers = list(node.modifiers) if node.modifiers else []

        interface_struct = CodeStructure(
            name=node.name,
            type=StructureType.INTERFACE,
            start_line=node.position.line,
            end_line=node.position.line + DEFAULT_INTERFACE_LINES,
            modifiers=modifiers
        )
        self.structures.append(interface_struct)

        # Process interface methods
        for method in node.methods:
            self._process_method(method, interface_struct)

        self._update_structure_end_line(interface_struct)

    def _process_enum(self, node: javalang.tree.EnumDeclaration,
                     path: List) -> None:
        """Process a Java enum declaration.

        Args:
            node: EnumDeclaration node from javalang.
            path: Path to this node in the AST.
        """
        if not hasattr(node, 'position') or not node.position:
            return

        modifiers = list(node.modifiers) if node.modifiers else []

        enum_struct = CodeStructure(
            name=node.name,
            type=StructureType.ENUM,
            start_line=node.position.line,
            end_line=node.position.line + DEFAULT_ENUM_LINES,
            modifiers=modifiers
        )
        self.structures.append(enum_struct)

        self._update_structure_end_line(enum_struct)

    def _process_constructor(self, node: javalang.tree.ConstructorDeclaration,
                            parent: CodeStructure) -> None:
        """Process a Java constructor.

        Args:
            node: ConstructorDeclaration node from javalang.
            parent: Parent class structure.
        """
        if not hasattr(node, 'position') or not node.position:
            return

        modifiers = list(node.modifiers) if node.modifiers else []
        params = self._extract_parameters(node.parameters)

        constructor_struct = CodeStructure(
            name=node.name,
            type=StructureType.CONSTRUCTOR,
            start_line=node.position.line,
            end_line=node.position.line + DEFAULT_CONSTRUCTOR_LINES,
            parent=parent,
            modifiers=modifiers,
            params=params
        )
        self.structures.append(constructor_struct)

        self._update_structure_end_line(constructor_struct)

    def _process_method(self, node: javalang.tree.MethodDeclaration,
                       parent: CodeStructure) -> None:
        """Process a Java method.

        Args:
            node: MethodDeclaration node from javalang.
            parent: Parent class/interface structure.
        """
        if not hasattr(node, 'position') or not node.position:
            return

        modifiers = list(node.modifiers) if node.modifiers else []
        params = self._extract_parameters(node.parameters)

        return_type = ''
        if node.return_type:
            return_type = (node.return_type.name if hasattr(node.return_type, 'name')
                          else str(node.return_type))

        method_struct = CodeStructure(
            name=node.name,
            type=StructureType.METHOD,
            start_line=node.position.line,
            end_line=node.position.line + DEFAULT_METHOD_LINES,
            parent=parent,
            modifiers=modifiers,
            params=params,
            return_type=return_type
        )
        self.structures.append(method_struct)

        self._update_structure_end_line(method_struct)

    def _find_parent_structure(self, path: List, node: javalang.tree.Node,
                              struct_type: StructureType) -> Optional[CodeStructure]:
        """Find the parent structure in the AST path.

        Args:
            path: Path to the current node in the AST.
            node: Current node.
            struct_type: Type of structure to look for.

        Returns:
            Parent CodeStructure if found, None otherwise.
        """
        for item in path:
            if isinstance(item, javalang.tree.ClassDeclaration) and item != node:
                # Find corresponding CodeStructure
                for struct in self.structures:
                    if struct.name == item.name and struct.type == struct_type:
                        return struct
        return None

    def _extract_parameters(self, parameters: Optional[List]) -> List[str]:
        """Extract parameter strings from a parameter list.

        Args:
            parameters: List of parameter nodes from javalang.

        Returns:
            List of formatted parameter strings.
        """
        if not parameters:
            return []

        params = []
        for param in parameters:
            param_type = (param.type.name if hasattr(param.type, 'name')
                         else str(param.type))
            params.append(f"{param_type} {param.name}")
        return params

    def _update_structure_end_line(self, struct: CodeStructure) -> None:
        """Update structure end line by matching braces.

        This method finds the closing brace that matches the opening brace
        of the structure to accurately determine its extent.

        Args:
            struct: CodeStructure to update.
        """
        lines = self.content.split('\n')
        start_idx = struct.start_line - 1

        if start_idx >= len(lines):
            struct.end_line = min(struct.start_line + FALLBACK_END_LINE_ESTIMATE, len(lines))
            return

        # Find matching braces
        brace_count = 0
        found_start = False

        for i in range(start_idx, len(lines)):
            line = lines[i]

            # Skip string literals and comments (simplified)
            in_string = False
            in_comment = False

            for j, char in enumerate(line):
                # Simple string detection (doesn't handle escapes perfectly)
                if char == '"' and (j == 0 or line[j-1] != '\\'):
                    in_string = not in_string

                # Skip if in string
                if in_string:
                    continue

                # Comment detection
                if char == '/' and j + 1 < len(line) and line[j+1] == '/':
                    in_comment = True
                    break

                if char == '{':
                    brace_count += 1
                    found_start = True
                elif char == '}':
                    brace_count -= 1
                    if found_start and brace_count == 0:
                        struct.end_line = i + 1
                        return

        # Fallback if no matching brace found
        struct.end_line = min(struct.start_line + FALLBACK_END_LINE_ESTIMATE, len(lines))


# ============================================================================
# Diff Analysis
# ============================================================================

class DiffAnalyzer:
    """Analyzes Git diffs and maps changes to code structures.

    Attributes:
        repo_path: Path to the Git repository.
        analyzers: Cache of file analyzers.
    """

    def __init__(self, repo_path: str = '.'):
        """Initialize the diff analyzer.

        Args:
            repo_path: Path to the Git repository.
        """
        self.repo_path = repo_path
        self.analyzers: Dict[str, LanguageAnalyzer] = {}

    def get_diff(self, commit1: Optional[str] = None,
                 commit2: Optional[str] = None) -> str:
        """Get Git diff output.

        Args:
            commit1: First commit/branch reference.
            commit2: Second commit/branch reference.

        Returns:
            Git diff output as a string.

        Raises:
            GitError: If git command fails.
        """
        cmd = ['git', 'diff']
        if commit1:
            cmd.append(commit1)
            if commit2:
                cmd.append(commit2)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=self.repo_path
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            raise GitError(f"Git command failed: {e}") from e

    def compare_files(self, file1: str, file2: str) -> str:
        """Compare two files directly using diff command.

        Args:
            file1: Path to first file.
            file2: Path to second file.

        Returns:
            Unified diff output.

        Raises:
            GitError: If file comparison fails.
        """
        from pathlib import Path

        path1 = Path(file1)
        path2 = Path(file2)

        if not path1.exists():
            raise GitError(f"File not found: {file1}")
        if not path2.exists():
            raise GitError(f"File not found: {file2}")

        try:
            # Use diff command to generate unified diff
            # -a forces text mode, -u for unified format
            cmd = ['diff', '-a', '-u', str(path1), str(path2)]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=self.repo_path
            )

            # diff returns exit code 1 when files differ (not an error)
            if result.returncode > 1:
                raise GitError(f"Diff command failed with code {result.returncode}")

            # Convert diff output to git-style format
            diff_output = result.stdout
            if diff_output:
                # Replace file paths with actual paths for analyzer
                lines = diff_output.split('\n')
                git_style = []
                for line in lines:
                    if line.startswith('---'):
                        git_style.append(f"--- a/{str(path1)}")
                    elif line.startswith('+++'):
                        git_style.append(f"+++ b/{str(path2)}")
                    else:
                        git_style.append(line)
                return '\n'.join(git_style)

            return diff_output

        except FileNotFoundError:
            raise GitError("'diff' command not found. Please install diffutils.")

    def analyze(self, diff_text: str) -> Dict[str, List[DiffChange]]:
        """Analyze diff text and map changes to code structures.

        Args:
            diff_text: Output from git diff.

        Returns:
            Dictionary mapping file paths to lists of DiffChange objects.
        """
        changes: Dict[str, List[DiffChange]] = {}
        current_file: Optional[str] = None
        current_file_old: Optional[str] = None
        current_line_new = 0  # Line number in new version
        current_line_old = 0  # Line number in old version
        current_analyzer_new: Optional[LanguageAnalyzer] = None
        current_analyzer_old: Optional[LanguageAnalyzer] = None

        for line in diff_text.split('\n'):
            # Parse old file path
            if line.startswith('---'):
                file_path_old = line.split('---')[1].strip()
                if file_path_old.startswith('a/'):
                    file_path_old = file_path_old[2:]
                current_file_old = file_path_old
                # Get old file content from git and create analyzer
                old_content = self._get_old_file_content(file_path_old)
                if old_content:
                    current_analyzer_old = self._get_or_create_analyzer(
                        file_path_old,
                        content=old_content,
                        cache_key=f"{file_path_old}:old"
                    )
                else:
                    # Fallback to using new file analyzer (less accurate but better than nothing)
                    current_analyzer_old = None
                continue

            # Parse new file path
            if line.startswith('+++'):
                file_path = line.split('+++')[1].strip()
                if file_path.startswith(DIFF_FILE_PREFIX):
                    file_path = file_path[len(DIFF_FILE_PREFIX):]

                current_file = file_path
                current_analyzer_new = self._get_or_create_analyzer(file_path)

                # If we don't have an old analyzer yet, fall back to new analyzer
                # This handles cases where git isn't available
                if current_analyzer_old is None and current_file_old:
                    current_analyzer_old = current_analyzer_new
                continue

            # Parse hunk headers
            if line.startswith('@@'):
                # Extract both old and new line numbers
                match = re.match(r'@@ -(\d+),?\d* \+(\d+),?\d* @@', line)
                if match:
                    current_line_old = int(match.group(1))
                    current_line_new = int(match.group(2))
                continue

            # Process added lines
            if line.startswith('+') and not line.startswith('+++'):
                if current_file and current_analyzer_new:
                    structure = current_analyzer_new.get_structure_at_line(current_line_new)

                    change = DiffChange(
                        file_path=current_file,
                        line_num=current_line_new,
                        content=line[1:],  # Keep original formatting
                        structure=structure,
                        change_type='+'
                    )

                    if current_file not in changes:
                        changes[current_file] = []
                    changes[current_file].append(change)

                current_line_new += 1

            # Process deleted lines
            elif line.startswith('-') and not line.startswith('---'):
                if current_file and current_analyzer_old:
                    structure = current_analyzer_old.get_structure_at_line(current_line_old)

                    change = DiffChange(
                        file_path=current_file,
                        line_num=current_line_old,
                        content=line[1:],  # Keep original formatting
                        structure=structure,
                        change_type='-'
                    )

                    if current_file not in changes:
                        changes[current_file] = []
                    changes[current_file].append(change)

                current_line_old += 1

            # Context lines
            elif not line.startswith('\\'):
                current_line_new += 1
                current_line_old += 1

        return changes

    def _get_old_file_content(self, file_path: str) -> Optional[str]:
        """Get old file content from git.

        Args:
            file_path: Path to the file.

        Returns:
            Content of the old file from git HEAD, or None if unavailable.
        """
        try:
            result = subprocess.run(
                ['git', 'show', f'HEAD:{file_path}'],
                capture_output=True,
                text=True,
                cwd=self.repo_path,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    def _get_file_lines(self, file_path: str, is_old_version: bool = False) -> List[str]:
        """Get all lines from a file.

        Args:
            file_path: Path to the file.
            is_old_version: If True, get old version from git; otherwise current file.

        Returns:
            List of lines in the file.
        """
        if is_old_version:
            content = self._get_old_file_content(file_path)
        else:
            # Try relative to repo path first
            full_path = Path(self.repo_path) / file_path
            if not full_path.exists():
                full_path = Path(file_path)

            if not full_path.exists():
                return []

            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception:
                return []

        if content:
            return content.split('\n')
        return []

    def _get_or_create_analyzer(self, file_path: str, content: Optional[str] = None, cache_key: Optional[str] = None) -> Optional[LanguageAnalyzer]:
        """Get or create a file analyzer with caching.

        Args:
            file_path: Relative path to the source file.
            content: Optional file content to analyze (instead of reading from disk).
            cache_key: Optional cache key (default: file_path).

        Returns:
            LanguageAnalyzer instance or None if unsupported/unavailable.
        """
        # Use custom cache key or default to file path
        key = cache_key if cache_key else file_path

        # Return cached analyzer
        if key in self.analyzers:
            return self.analyzers[key]

        # If content provided, use it directly
        if content is not None:
            file_content = content
        else:
            # Try relative to repo path first
            full_path = Path(self.repo_path) / file_path

            # If not found, try as absolute path
            if not full_path.exists():
                full_path = Path(file_path)

            if not full_path.exists():
                logger.debug(f"File not found: {file_path}")
                return None

            # Read file content
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    file_content = f.read()
            except Exception as e:
                logger.warning(f"Failed to read file {file_path}: {e}")
                return None

        # Select analyzer based on file extension
        path = Path(file_path)
        suffix = path.suffix.lower()
        analyzer: Optional[LanguageAnalyzer] = None

        if suffix == '.py':
            analyzer = PythonAnalyzer(file_path, file_content)
        elif suffix == '.java':
            if JAVALANG_AVAILABLE:
                analyzer = JavaAnalyzer(file_path, file_content)
            else:
                logger.warning(f"Java analyzer unavailable, skipping {file_path}")
                return None
        else:
            return None

        # Parse and cache
        if analyzer and analyzer.parse():
            self.analyzers[key] = analyzer
            return analyzer

        return None


# ============================================================================
# Output Formatting
# ============================================================================

class ResultPrinter:
    """Formats and prints analysis results to the console."""

    @staticmethod
    def print_results(changes: Dict[str, List[DiffChange]],
                     verbose: bool = False,
                     analyzer: Optional['DiffAnalyzer'] = None) -> None:
        """Print analysis results grouped by file and structure.

        Args:
            changes: Dictionary mapping file paths to change lists.
            verbose: Show more detailed output if True.
            analyzer: DiffAnalyzer instance for accessing file content.
        """
        if not changes:
            print(f"\n{Fore.YELLOW}No supported file changes detected")
            return

        print(f"\n{Fore.CYAN}{'=' * SEPARATOR_WIDTH}")
        print(f"{Fore.CYAN}AST Diff Analysis Results")
        print(f"{Fore.CYAN}{'=' * SEPARATOR_WIDTH}\n")

        total_changes = sum(len(file_changes) for file_changes in changes.values())
        print(f"{Fore.GREEN}Summary: {len(changes)} files, {total_changes} lines changed\n")

        for file_path, file_changes in changes.items():
            ResultPrinter._print_file_changes(file_path, file_changes, verbose, analyzer)

    @staticmethod
    def _print_file_changes(file_path: str, changes: List[DiffChange],
                           verbose: bool, analyzer: Optional['DiffAnalyzer'] = None) -> None:
        """Print changes for a single file.

        Args:
            file_path: Path to the file.
            changes: List of changes in the file.
            verbose: Show more detailed output if True.
            analyzer: DiffAnalyzer instance for accessing file content.
        """
        print(f"{Fore.CYAN}File: {file_path}")
        print(f"{Fore.CYAN}{'-' * SEPARATOR_WIDTH}")

        # Group changes by structure
        by_structure: Dict[str, List[DiffChange]] = {}
        for change in changes:
            if change.structure:
                key = change.structure.get_full_path()
            else:
                key = 'unknown structure'

            if key not in by_structure:
                by_structure[key] = []
            by_structure[key].append(change)

        # Get file lines for context
        file_lines_new = []
        file_lines_old = []
        if analyzer:
            file_lines_new = analyzer._get_file_lines(file_path, is_old_version=False)
            file_lines_old = analyzer._get_file_lines(file_path, is_old_version=True)

        # Print each structure's changes
        for struct_path, struct_changes in sorted(by_structure.items()):
            # Count additions and deletions
            additions = sum(1 for c in struct_changes if c.change_type == '+')
            deletions = sum(1 for c in struct_changes if c.change_type == '-')

            print(f"\n  {Fore.GREEN}▸ {struct_path}")
            print(f"     {Fore.YELLOW}(+{additions} -{deletions} lines)")

            # Sort changes by line number to display them in order
            sorted_changes = sorted(struct_changes, key=lambda c: c.line_num)

            # Build a map of changed lines (line_num -> list of changes)
            change_map = {}
            for change in sorted_changes:
                if change.line_num not in change_map:
                    change_map[change.line_num] = []
                change_map[change.line_num].append(change)

            # Collect all line numbers that need to be displayed
            CONTEXT_LINES = 3
            lines_to_display = set()

            for line_num in change_map.keys():
                # Add the changed line
                lines_to_display.add(line_num)

                # Add context lines before and after
                for offset in range(1, CONTEXT_LINES + 1):
                    # Context before
                    ctx_line_num = line_num - offset
                    if ctx_line_num > 0:
                        lines_to_display.add(ctx_line_num)

                    # Context after
                    ctx_line_num = line_num + offset
                    lines_to_display.add(ctx_line_num)

            # Sort all lines to display
            sorted_lines = sorted(lines_to_display)

            # Display lines with context
            prev_line_num = None
            for line_num in sorted_lines:
                # Show ... if there's a gap
                if prev_line_num is not None and line_num - prev_line_num > 1:
                    if line_num - prev_line_num >= MIN_GAP_FOR_ELLIPSIS:
                        print(f"     {Fore.YELLOW}     ...")

                # Check if this is a changed line or context
                if line_num in change_map:
                    changes_at_line = change_map[line_num]

                    # Check if this line has both + and - with identical content
                    # If so, treat it as unchanged (just formatting/whitespace difference)
                    if len(changes_at_line) == 2:
                        contents = [c.content.rstrip() for c in changes_at_line]
                        types = [c.change_type for c in changes_at_line]

                        # If one + and one - with same content, show as context
                        if '+' in types and '-' in types and contents[0] == contents[1]:
                            # Display as context line (no +/- symbol)
                            content = contents[0]
                            print(f"     {Fore.WHITE}{line_num:4d}   {content}")
                            prev_line_num = line_num
                            continue

                    # Display all changes at this line
                    for change in changes_at_line:
                        content = change.content.rstrip()

                        # Color code based on change type
                        if change.change_type == '+':
                            color = Fore.GREEN
                            prefix = '+'
                        else:
                            color = Fore.RED
                            prefix = '-'

                        # Format: line_num +/- content
                        print(f"     {Fore.WHITE}{line_num:4d} {color}{prefix} {content}")
                else:
                    # Context line - get from appropriate file version
                    # Use new file for context (could also use old file for deletions)
                    file_lines = file_lines_new if file_lines_new else file_lines_old
                    if file_lines and 1 <= line_num <= len(file_lines):
                        content = file_lines[line_num - 1].rstrip()
                        # Format: line_num   content (no +/- symbol)
                        print(f"     {Fore.WHITE}{line_num:4d}   {content}")

                prev_line_num = line_num

        print()

    @staticmethod
    def print_statistics(changes: Dict[str, List[DiffChange]]) -> None:
        """Print statistical summary of changes.

        Args:
            changes: Dictionary mapping file paths to change lists.
        """
        print(f"\n{Fore.CYAN}{'=' * SEPARATOR_WIDTH}")
        print(f"{Fore.CYAN}Statistics")
        print(f"{Fore.CYAN}{'=' * SEPARATOR_WIDTH}\n")

        # Count changes per structure
        structure_stats: Dict[str, int] = {}

        for file_changes in changes.values():
            for change in file_changes:
                if change.structure:
                    key = change.structure.get_full_path()
                    structure_stats[key] = structure_stats.get(key, 0) + 1

        # Sort by change count
        sorted_stats = sorted(
            structure_stats.items(),
            key=lambda x: x[1],
            reverse=True
        )

        print(f"{Fore.GREEN}Top {TOP_STRUCTURES_LIMIT} Most Modified Structures:\n")
        for i, (struct_path, count) in enumerate(sorted_stats[:TOP_STRUCTURES_LIMIT], 1):
            print(f"  {i:2d}. {Fore.YELLOW}{count:3d} lines{Fore.WHITE} | {struct_path}")

        print()


# ============================================================================
# CLI Interface
# ============================================================================

def validate_dependencies() -> None:
    """Check and warn about missing dependencies."""
    if not JAVALANG_AVAILABLE:
        logger.warning("javalang not installed - Java analysis unavailable")
        logger.info("Install with: pip install javalang")


def main() -> None:
    """Main entry point for the CLI application."""
    import argparse

    parser = argparse.ArgumentParser(
        description='AST Diff Analyzer - Precisely analyze Java and Python code changes',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Git mode (default)
  %(prog)s                          # Analyze working directory changes
  %(prog)s HEAD~1 HEAD              # Compare two commits
  %(prog)s main feature-branch      # Compare branches
  %(prog)s -v                       # Verbose mode
  %(prog)s -s                       # Show statistics
  %(prog)s --repo /path/to/repo     # Specify repository path

  # File comparison mode (no git required)
  %(prog)s --compare file1.py file2.py              # Compare two files
  %(prog)s --compare old/test.py new/test.py -v     # Verbose comparison
  %(prog)s --compare v1.java v2.java -s             # With statistics
        """
    )

    parser.add_argument(
        'commit1',
        nargs='?',
        help='First commit/branch reference (or first file with --compare)'
    )
    parser.add_argument(
        'commit2',
        nargs='?',
        help='Second commit/branch reference (or second file with --compare)'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Show more detailed change information'
    )
    parser.add_argument(
        '-s', '--stats',
        action='store_true',
        help='Display statistics summary'
    )
    parser.add_argument(
        '--compare',
        action='store_true',
        help='Compare two files directly (no git required)'
    )
    parser.add_argument(
        '--repo',
        default='.',
        help='Git repository path (default: current directory)'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )

    args = parser.parse_args()

    # Configure logging
    if args.debug:
        logger.setLevel(logging.DEBUG)

    # Validate dependencies
    validate_dependencies()

    try:
        # Create analyzer
        analyzer = DiffAnalyzer(args.repo)

        # Get diff
        if args.compare:
            # File comparison mode
            if not args.commit1 or not args.commit2:
                logger.error("--compare requires two file paths")
                print(f"\n{Fore.RED}Error: --compare requires two file paths")
                print(f"{Fore.YELLOW}Usage: {sys.argv[0]} --compare file1.py file2.py")
                sys.exit(1)

            print(f"\n{Fore.CYAN}Comparing files: {args.commit1} vs {args.commit2}")
            diff_text = analyzer.compare_files(args.commit1, args.commit2)
        else:
            # Git mode
            diff_text = analyzer.get_diff(args.commit1, args.commit2)

        if not diff_text.strip():
            print(f"\n{Fore.YELLOW}No changes detected")
            return

        # Analyze diff
        print(f"\n{Fore.CYAN}Analyzing changes...")
        changes = analyzer.analyze(diff_text)

        # Print results
        ResultPrinter.print_results(changes, args.verbose, analyzer)

        # Print statistics if requested
        if args.stats:
            ResultPrinter.print_statistics(changes)

    except GitError as e:
        logger.error(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        if args.debug:
            raise
        sys.exit(1)


if __name__ == '__main__':
    main()
