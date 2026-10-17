import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';
export default defineConfig({plugins:[react(),tailwind()],server:{port:5174,strictPort:true,proxy:{'/api':'http://127.0.0.1:8018','/docs':'http://127.0.0.1:8018','/openapi.json':'http://127.0.0.1:8018'}}});
