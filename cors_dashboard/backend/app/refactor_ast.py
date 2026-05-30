import ast
import astunparse
import os

MAIN_FILE = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app\main.py'
ROUTERS_DIR = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app\routers'

with open(MAIN_FILE, 'r', encoding='utf-8') as f:
    source = f.read()

tree = ast.parse(source)

# We will collect functions that have @app. decorators
# and group them by prefix.

class EndpointVisitor(ast.NodeVisitor):
    def __init__(self):
        self.endpoints = []

    def visit_AsyncFunctionDef(self, node):
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if isinstance(dec.func.value, ast.Name) and dec.func.value.id == 'app':
                    # It's an @app.get/post/etc
                    if len(dec.args) > 0 and isinstance(dec.args[0], ast.Constant):
                        path = dec.args[0].value
                        self.endpoints.append({
                            'node': node,
                            'path': path,
                            'method': dec.func.attr
                        })
        self.generic_visit(node)

visitor = EndpointVisitor()
visitor.visit(tree)

print(f"Found {len(visitor.endpoints)} endpoints.")

# For simplicity, we won't rewrite main.py using astunparse because it ruins comments and formatting.
# We will just print out the line numbers so we can extract them using string manipulation.
for ep in visitor.endpoints:
    print(f"{ep['method'].upper()} {ep['path']} - Line: {ep['node'].lineno} to {ep['node'].end_lineno}")

