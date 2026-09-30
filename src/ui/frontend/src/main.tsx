import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import { MechanicFlow } from './components/mechanic/MechanicFlow';
import { SignInGate } from './components/mechanic/SignInGate';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <SignInGate>
      <MechanicFlow>
        <App />
      </MechanicFlow>
    </SignInGate>
  </React.StrictMode>
);
