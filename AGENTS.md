# AGENTS.md - TerraSentinel 3D (Earth Science & Disaster Command System)

## Arquitectura y Stack Tecnologico
- **Backend:** Python 3.11+, FastAPI, Uvicorn, SQLite3 (`aiosqlite` o `sqlite3`), `httpx` para peticiones asincronas, `pydantic` v2.
- **Frontend:** SPA modular (HTML5, CSS3 estilo Cyberpunk/NASA Mission Control HUD, JavaScript ES6+ con **CesiumJS** o **Three.js / Globe.gl** via CDN para renderizado 3D instantaneo sin problemas de compilacion).
- **Base de Datos y Resguardo:** SQLite3 local (`data/terrasentinel.db`) para almacenar snapshots historicos de telemetria, comparar cambios temporales y persistir simulaciones de impacto.

## Estandares de Codigo
- Arquitectura limpia separada en: `backend/api/`, `backend/services/`, `backend/models/`, `backend/database/`, `frontend/js/`, `frontend/css/`.
- Tipado estatico estricto en Python (`typing`, `Pydantic`).
- Manejo robusto de errores: si una API satelital externa tarda o falla, el backend debe usar datos en cache de SQLite o un generador de respaldo realista para que la demo en vivo nunca se caiga.
- Codigo limpio, modular y listo para ejecutarse con un solo comando.