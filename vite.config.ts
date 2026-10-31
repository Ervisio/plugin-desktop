import { defineConfig } from 'vite';
import { ervisioPlugin } from '@ervisio/plugin-sdk/vite';
export default defineConfig(ervisioPlugin({ id: 'desktop', entry: 'src/index.tsx' }));
