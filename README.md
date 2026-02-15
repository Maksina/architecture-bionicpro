1
1. схема
2. обновление app.tsx
const initOptions: KeycloakInitOptions = {
  onLoad: 'login-required', 
  pkceMethod: 'S256',
  checkLoginIframe: false, 
};
3 