# Frontend source

`js/` and `css/` contain editable browser source. FastAPI only serves named
HTML pages, built assets in `frontend/js/dist` and `frontend/css/dist`, and
images. These source files stay outside those public routes.

After editing, run `node scripts/build_frontend_assets.mjs` from the repository
root. Commit the generated files in `cors_dashboard/frontend/js/dist` and
`cors_dashboard/frontend/css/dist` for deployments that use `git pull`.

Browser JavaScript remains readable in principle even when minified. Keep
credentials and access checks on the backend.
