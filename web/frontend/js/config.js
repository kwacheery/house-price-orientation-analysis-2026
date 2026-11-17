/* 运行模式判断与统一数据加载：本地走 FastAPI，Pages 走预导出 JSON。 */
(function (global) {
  var params = new URLSearchParams(location.search);
  var forced = params.get('mode');
  var isPages = forced === 'static' ||
    (!forced && (location.hostname.endsWith('github.io') || location.protocol === 'file:'));

  var endpoints = {
    overview: 'overview',
    preprocessing: 'preprocessing',
    orientation: 'orientation',
    metrics: 'model/metrics',
    importance: 'model/importance',
    predictions: 'model/predictions'
  };

  async function load(name) {
    // 静态预测模型属于配置，两种模式都直接读导出的 JSON。
    var url = name === 'static_model'
      ? './data/static_model.json'
      : (isPages ? './data/' + name + '.json' : '/api/' + endpoints[name]);
    var response = await fetch(url, { cache: 'no-cache' });
    if (!response.ok) throw new Error(name + ' 加载失败：' + response.status);
    var data = await response.json();
    if (data && data.available === false) throw new Error(data.reason || name + ' 暂无数据');
    return data;
  }

  async function predict(payload) {
    if (isPages) return null;
    var response = await fetch('/api/predict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!response.ok) throw new Error('预测接口失败：' + response.status);
    var data = await response.json();
    if (data.available === false) throw new Error(data.reason || '预测失败');
    return data;
  }

  global.APP_CONFIG = { mode: isPages ? 'static' : 'local', load: load, predict: predict };
})(window);
