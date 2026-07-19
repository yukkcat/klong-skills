import { createApp } from 'vue'
import { nanocatZhCN, setNanocatLocale } from 'nanocat-ui'
import App from './App.vue'
import 'nanocat-ui/styles.css'
import './styles.css'

setNanocatLocale(nanocatZhCN)

createApp(App).mount('#app')
