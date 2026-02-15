import React from 'react';
import ReportPage from './components/ReportPage';

const App: React.FC = () => {
  return (
    <div className="App">
      <ReportPage />
    </div>
  );
};

export default App;

// import React from 'react';
// import { ReactKeycloakProvider } from '@react-keycloak/web';
// import Keycloak, { KeycloakInitOptions } from 'keycloak-js';
// import ReportPage from './components/ReportPage';
// 
// const keycloakConfig = {
//   url: process.env.REACT_APP_KEYCLOAK_URL,
//   realm: process.env.REACT_APP_KEYCLOAK_REALM || "",
//   clientId: process.env.REACT_APP_KEYCLOAK_CLIENT_ID || ""
// };
// 
// onst keycloak = new Keycloak(keycloakConfig);

// const initOptions: KeycloakInitOptions = {
//   onLoad: 'login-required', // или 'check-sso'
//   pkceMethod: 'S256',
//   checkLoginIframe: false, // Рекомендуется отключить при использовании PKCE
// };
// 
// const App: React.FC = () => {
//   return (
//     <ReactKeycloakProvider authClient={keycloak} initOptions={initOptions}>
//       <div className="App">
//         <ReportPage />
//       </div>
//     </ReactKeycloakProvider>
//   );
// };
// 
// export default App;