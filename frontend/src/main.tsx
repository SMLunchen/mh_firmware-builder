import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import ParticleBackground from './components/ParticleBackground'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ParticleBackground />
    <App />
  </React.StrictMode>,
)
