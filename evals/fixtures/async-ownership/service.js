'use strict';

let currentKey = null;
let rendered = null;

async function select(key, load) {
  currentKey = key;
  const data = await load(key);
  rendered = { key, data };
}

function snapshot() {
  return { currentKey, rendered };
}

module.exports = { select, snapshot };
