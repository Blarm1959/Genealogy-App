import { defineConfig } from 'vite';
export default defineConfig({root:'frontend',build:{outDir:'../dist',emptyOutDir:true,rollupOptions:{treeshake:false}},server:{proxy:{'/api':'http://127.0.0.1:8510'}}});
