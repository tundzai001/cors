import os
import ast

backend_dir = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app'

files_to_parse = [os.path.join(backend_dir, 'main.py')]
routers_dir = os.path.join(backend_dir, 'routers')
for f in os.listdir(routers_dir):
    if f.endswith('.py'):
        files_to_parse.append(os.path.join(routers_dir, f))

apis = []

for filepath in files_to_parse:
    with open(filepath, 'r', encoding='utf-8') as f:
        source = f.read()
    
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) or isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and getattr(dec.func, 'attr', None) in ['get', 'post', 'put', 'delete', 'patch', 'websocket']:
                        method = dec.func.attr.upper()
                        path = ''
                        if dec.args and isinstance(dec.args[0], ast.Constant):
                            path = dec.args[0].value
                        
                        docstring = ast.get_docstring(node) or ''
                        docstring = docstring.strip().split('\n')[0] if docstring else ''
                        
                        auth_dep = ''
                        for arg in node.args.args + node.args.kwonlyargs:
                            if arg.arg == 'current_user':
                                auth_dep = 'Requires Authentication'
                                
                        apis.append({
                            'file': os.path.basename(filepath),
                            'method': method,
                            'path': path,
                            'func': node.name,
                            'doc': docstring,
                            'auth': auth_dep
                        })
    except Exception as e:
        print(f'Error parsing {filepath}: {e}')

output = '# CORS Backend API Documentation\n\n'
for api in sorted(apis, key=lambda x: (x['file'], x['path'])):
    output += f'### `{api["method"]}` {api["path"]}\n'
    output += f'- **File**: {api["file"]}\n'
    output += f'- **Function**: `{api["func"]}`\n'
    if api['auth']:
        output += f'- **Auth**: {api["auth"]}\n'
    if api['doc']:
        output += f'- **Description**: {api["doc"]}\n'
    output += '\n'

with open(r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\api_docs.md', 'w', encoding='utf-8') as f:
    f.write(output)
print('API documentation generated at c:\\Users\\len\\Desktop\\other_projects\\cors\\cors_dashboard\\backend\\api_docs.md')
