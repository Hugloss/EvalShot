'use strict';

function startWork(commit) {
  setTimeout(() => commit('done'), 25);
}

module.exports = { startWork };
