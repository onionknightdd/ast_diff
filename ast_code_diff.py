#!/usr/bin/env python3
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
            parts.append(f"{modifier_str}{current.type} {current.name}")
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


@dataclass
class DiffChange:
    """Represents a single line change in a diff.

    Attributes:
        file_path: Path to the changed file.
        line_num: Line number in the file.
        content: Content of the changed line.
        structure: Code structure containing this change.
    """
    file_path: str
    line_num: int
    content: str
    structure: Optional[CodeStructure] = None


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

    def analyze(self, diff_text: str) -> Dict[str, List[DiffChange]]:
        """Analyze diff text and map changes to code structures.

        Args:
            diff_text: Output from git diff.

        Returns:
            Dictionary mapping file paths to lists of DiffChange objects.
        """
        changes: Dict[str, List[DiffChange]] = {}
        current_file: Optional[str] = None
        current_line = 0
        current_analyzer: Optional[LanguageAnalyzer] = None

        for line in diff_text.split('\n'):
            # Parse file paths
            if line.startswith('+++'):
                file_path = line.split('+++')[1].strip()
                if file_path.startswith(DIFF_FILE_PREFIX):
                    file_path = file_path[len(DIFF_FILE_PREFIX):]

                current_file = file_path
                current_analyzer = self._get_or_create_analyzer(file_path)
                continue

            # Parse hunk headers
            if line.startswith('@@'):
                match = re.match(DIFF_HUNK_PATTERN, line)
                if match:
                    current_line = int(match.group(1))
                continue

            # Process added lines
            if line.startswith('+') and not line.startswith('+++'):
                if current_file and current_analyzer:
                    structure = current_analyzer.get_structure_at_line(current_line)

                    change = DiffChange(
                        file_path=current_file,
                        line_num=current_line,
                        content=line[1:].rstrip(),
                        structure=structure
                    )

                    if current_file not in changes:
                        changes[current_file] = []
                    changes[current_file].append(change)

                current_line += 1

            # Skip deleted lines (don't increment line number)
            elif line.startswith('-') and not line.startswith('---'):
                continue

            # Context lines
            elif not line.startswith('\\'):
                current_line += 1

        return changes

    def _get_or_create_analyzer(self, file_path: str) -> Optional[LanguageAnalyzer]:
        """Get or create a file analyzer with caching.

        Args:
            file_path: Relative path to the source file.

        Returns:
            LanguageAnalyzer instance or None if unsupported/unavailable.
        """
        # Return cached analyzer
        if file_path in self.analyzers:
            return self.analyzers[file_path]

        full_path = Path(self.repo_path) / file_path

        if not full_path.exists():
            logger.debug(f"File not found: {file_path}")
            return None

        # Read file content
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            logger.warning(f"Failed to read file {file_path}: {e}")
            return None

        # Select analyzer based on file extension
        suffix = full_path.suffix.lower()
        analyzer: Optional[LanguageAnalyzer] = None

        if suffix == '.py':
            analyzer = PythonAnalyzer(file_path, content)
        elif suffix == '.java':
            if JAVALANG_AVAILABLE:
                analyzer = JavaAnalyzer(file_path, content)
            else:
                logger.warning(f"Java analyzer unavailable, skipping {file_path}")
                return None
        else:
            return None

        # Parse and cache
        if analyzer and analyzer.parse():
            self.analyzers[file_path] = analyzer
            return analyzer

        return None


# ============================================================================
# Output Formatting
# ============================================================================

class ResultPrinter:
    """Formats and prints analysis results to the console."""

    @staticmethod
    def print_results(changes: Dict[str, List[DiffChange]],
                     verbose: bool = False) -> None:
        """Print analysis results grouped by file and structure.

        Args:
            changes: Dictionary mapping file paths to change lists.
            verbose: Show more detailed output if True.
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
            ResultPrinter._print_file_changes(file_path, file_changes, verbose)

    @staticmethod
    def _print_file_changes(file_path: str, changes: List[DiffChange],
                           verbose: bool) -> None:
        """Print changes for a single file.

        Args:
            file_path: Path to the file.
            changes: List of changes in the file.
            verbose: Show more detailed output if True.
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

        # Print each structure's changes
        for struct_path, struct_changes in sorted(by_structure.items()):
            print(f"\n  {Fore.GREEN}▸ {struct_path}")
            print(f"     {Fore.YELLOW}({len(struct_changes)} lines changed)")

            # Display changed lines
            display_limit = (VERBOSE_MAX_LINES_DISPLAY if verbose
                           else DEFAULT_MAX_LINES_DISPLAY)

            for change in struct_changes[:display_limit]:
                content = change.content
                if len(content) > MAX_LINE_LENGTH:
                    content = content[:MAX_LINE_LENGTH - 3] + "..."

                print(f"     {Fore.WHITE}L{change.line_num:4d}: {content}")

            if len(struct_changes) > display_limit:
                remaining = len(struct_changes) - display_limit
                print(f"     {Fore.YELLOW}... {remaining} more lines")

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
  %(prog)s                          # Analyze working directory changes
  %(prog)s HEAD~1 HEAD              # Compare two commits
  %(prog)s main feature-branch      # Compare branches
  %(prog)s -v                       # Verbose mode
  %(prog)s -s                       # Show statistics
  %(prog)s --repo /path/to/repo     # Specify repository path
        """
    )

    parser.add_argument(
        'commit1',
        nargs='?',
        help='First commit/branch reference'
    )
    parser.add_argument(
        'commit2',
        nargs='?',
        help='Second commit/branch reference'
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
        diff_text = analyzer.get_diff(args.commit1, args.commit2)

        if not diff_text.strip():
            print(f"\n{Fore.YELLOW}No changes detected")
            return

        # Analyze diff
        print(f"\n{Fore.CYAN}Analyzing changes...")
        changes = analyzer.analyze(diff_text)

        # Print results
        ResultPrinter.print_results(changes, args.verbose)

        # Print statistics if requested
        if args.stats:
            ResultPrinter.print_statistics(changes)

    except GitError as e:
        logger.error(f"Git error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        if args.debug:
            raise
        sys.exit(1)


if __name__ == '__main__':
    main()
