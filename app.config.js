const path = require('path');

// Expo only auto-loads `.env` next to `package.json`. Also load repo root `.env` so one file can drive both apps.
require('dotenv').config({ path: path.resolve(__dirname, '..', '.env') });
require('dotenv').config({ path: path.resolve(__dirname, '.env') });

module.exports = require('./app.json');
