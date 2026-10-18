'use strict';

function canRun(config) {
  return config.enabled === true && config.blocked !== true;
}

module.exports = { canRun };
