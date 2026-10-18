'use strict';

const { startWork } = require('./worker');

async function sleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function testBackgroundCommit() {
  let result = null;
  startWork((value) => { result = value; });
  await sleep(500);
  if (result !== 'done') throw new Error('expected background commit');
}

module.exports = { testBackgroundCommit };
