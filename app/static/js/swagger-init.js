// Swagger UI initializer — Phase 16.5.
//
// Loaded by app/templates/api/docs.html as an external script so the
// page never relies on CSP 'unsafe-inline' for script-src. Points the
// UI at the same-origin /api/v1/openapi.yaml endpoint served by
// app.routes.api.
window.addEventListener('load', function () {
  if (typeof SwaggerUIBundle !== 'function') {
    return;
  }
  // eslint-disable-next-line no-undef
  window.ui = SwaggerUIBundle({
    url: '/api/v1/openapi.yaml',
    dom_id: '#swagger-ui',
    deepLinking: true,
    presets: [
      // eslint-disable-next-line no-undef
      SwaggerUIBundle.presets.apis,
      // eslint-disable-next-line no-undef
      SwaggerUIStandalonePreset,
    ],
    layout: 'StandaloneLayout',
    docExpansion: 'list',
    defaultModelsExpandDepth: 1,
    tryItOutEnabled: true,
  });
});
