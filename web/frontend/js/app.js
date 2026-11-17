/* 前端主逻辑：加载真实数据、绘制图表、磁吸交互与预测。 */
(function () {
  var CFG = window.APP_CONFIG;
  var $ = function (selector) { return document.querySelector(selector); };
  var $$ = function (selector) { return Array.prototype.slice.call(document.querySelectorAll(selector)); };
  var fmt = function (value) { return Number(value || 0).toLocaleString('zh-CN'); };
  var charts = [];

  var COLORS = {
    accent: '#2f8f83',
    accentSoft: '#bfe2dc',
    coral: '#e28770',
    blue: '#7ba7c9',
    sand: '#d8b27a',
    text: '#2d3b45',
    muted: '#8293a0',
    grid: '#d7dfe6'
  };

  function baseOption(extra) {
    return Object.assign({
      backgroundColor: 'transparent',
      animationDuration: 700,
      animationEasing: 'cubicOut',
      textStyle: { color: COLORS.muted, fontFamily: 'Inter, "Microsoft YaHei", sans-serif' },
      tooltip: { backgroundColor: 'rgba(255,255,255,.96)', borderColor: COLORS.grid, textStyle: { color: COLORS.text } }
    }, extra || {});
  }

  function draw(id, option) {
    var el = document.getElementById(id);
    if (!el || typeof echarts === 'undefined') return null;
    var instance = echarts.init(el, null, { renderer: 'canvas' });
    instance.setOption(baseOption(option));
    charts.push(instance);
    return instance;
  }

  function axisStyle() {
    return {
      axisLine: { lineStyle: { color: COLORS.grid } },
      axisTick: { show: false },
      axisLabel: { color: COLORS.muted, fontSize: 10 },
      splitLine: { lineStyle: { color: COLORS.grid, type: 'dashed', opacity: 0.55 } }
    };
  }

  /* ---------- 01 概况 ---------- */
  function renderOverview(data) {
    var totals = data.totals || {};
    $$('[data-stat]').forEach(function (el) {
      var key = el.getAttribute('data-stat');
      if (key === 'split') {
        el.textContent = fmt(totals.train) + ' / ' + fmt(totals.test);
      } else {
        el.textContent = fmt(totals[key]);
      }
    });
    $('#overviewNote').textContent = '数据来源：房天下广州、链家/贝壳多城市首页 · 共 ' +
      fmt(data.city_count) + ' 个城市 · 更新于 ' + (data.generated_at || '').slice(0, 10);

    var sources = data.sources || [];
    draw('chartSources', {
      tooltip: { trigger: 'item', formatter: '{b}: {c} 条 ({d}%)' },
      legend: { bottom: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 10 } },
      color: [COLORS.accent, COLORS.blue, COLORS.coral],
      series: [{
        type: 'pie', radius: ['46%', '72%'], center: ['50%', '43%'],
        itemStyle: { borderColor: '#e9eef3', borderWidth: 4, borderRadius: 6 },
        label: { formatter: '{b}\n{c}', fontSize: 10, color: COLORS.text },
        data: sources.map(function (item) { return { name: item.source, value: item.records }; })
      }]
    });

    var districts = Object.keys(data.district_counts || {}).map(function (key) {
      return { name: key, value: data.district_counts[key] };
    }).sort(function (a, b) { return a.value - b.value; });
    draw('chartDistricts', {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 70, right: 20, top: 10, bottom: 24 },
      xAxis: Object.assign({ type: 'value' }, axisStyle()),
      yAxis: Object.assign({ type: 'category', data: districts.map(function (d) { return d.name; }) }, axisStyle(), { splitLine: { show: false } }),
      series: [{
        type: 'bar', data: districts.map(function (d) { return d.value; }),
        barWidth: '58%',
        itemStyle: { color: COLORS.accent, borderRadius: [0, 6, 6, 0] }
      }]
    });
  }

  /* ---------- 02 预处理 ---------- */
  function renderPreprocessing(data) {
    var steps = data.summary || [];
    $('#prepOutliers').textContent = fmt((data.outliers || {}).rows);
    $('#prepSuspect').textContent = fmt(data.unit_price_suspect);
    $('#prepVillas').textContent = fmt(data.villas);
    $('#prepCross').textContent = fmt(data.cross_source_dup || 0);

    draw('chartSteps', {
      tooltip: { trigger: 'axis' },
      grid: { left: 46, right: 16, top: 16, bottom: 78 },
      xAxis: Object.assign({ type: 'category', data: steps.map(function (s) { return s.step; }), axisLabel: { rotate: 38, fontSize: 9, color: COLORS.muted } }, axisStyle(), { splitLine: { show: false } }),
      yAxis: Object.assign({ type: 'value', min: 3200 }, axisStyle()),
      series: [{
        type: 'bar', data: steps.map(function (s) { return s.rows_out; }),
        barWidth: '55%',
        itemStyle: { color: COLORS.accent, borderRadius: [5, 5, 0, 0] }
      }]
    });

    var fields = ['area', 'orientation', 'district', 'community', 'total_price', 'unit_price']
      .filter(function (key) { return data.after && data.after[key] !== undefined; });
    draw('chartCompleteness', {
      tooltip: { trigger: 'axis', valueFormatter: function (v) { return (v * 100).toFixed(1) + '%'; } },
      legend: { top: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 10 } },
      grid: { left: 46, right: 16, top: 34, bottom: 24 },
      xAxis: Object.assign({ type: 'category', data: fields }, axisStyle(), { splitLine: { show: false } }),
      yAxis: Object.assign({ type: 'value', min: 0, max: 1, axisLabel: { formatter: function (v) { return (v * 100) + '%'; }, fontSize: 10, color: COLORS.muted } }, axisStyle()),
      series: [
        { name: '清洗前', type: 'bar', data: fields.map(function (f) { return (data.before || {})[f] || 0; }), itemStyle: { color: COLORS.sand, borderRadius: [4, 4, 0, 0] }, barWidth: '28%' },
        { name: '清洗后', type: 'bar', data: fields.map(function (f) { return data.after[f]; }), itemStyle: { color: COLORS.accent, borderRadius: [4, 4, 0, 0] }, barWidth: '28%' }
      ]
    });
  }

  /* ---------- 03 朝向 ---------- */
  function renderOrientation(data) {
    var dist = data.distribution || [];
    draw('chartOrientationCount', {
      tooltip: { trigger: 'axis' },
      grid: { left: 40, right: 16, top: 12, bottom: 26 },
      xAxis: Object.assign({ type: 'category', data: dist.map(function (d) { return d.orientation; }) }, axisStyle(), { splitLine: { show: false } }),
      yAxis: Object.assign({ type: 'value' }, axisStyle()),
      series: [{
        type: 'bar', barWidth: '55%',
        data: dist.map(function (d) { return d.n; }),
        itemStyle: { color: function (p) { return p.dataIndex === 0 ? COLORS.coral : COLORS.accent; }, borderRadius: [5, 5, 0, 0] }
      }]
    });

    var premium = (data.premium || []).filter(function (p) { return p.orientation !== data.reference; });
    var values = premium.map(function (p) { return (p.premium || 0) * 100; });
    var low = Math.min.apply(null, values.concat([0]));
    var high = Math.max.apply(null, values.concat([0]));
    var margin = (high - low) * 0.15 || 5;
    draw('chartPremium', {
      tooltip: {
        trigger: 'item',
        formatter: function (p) {
          var item = premium[p.dataIndex];
          return item.orientation + '<br/>溢价 ' + (item.premium * 100).toFixed(1) + '%<br/>p = ' + Number(item.p_value).toFixed(3);
        }
      },
      grid: { left: 50, right: 22, top: 10, bottom: 26 },
      xAxis: Object.assign({ type: 'value', min: Math.floor(low - margin), max: Math.ceil(high + margin), axisLabel: { formatter: '{value}%', fontSize: 10, color: COLORS.muted } }, axisStyle()),
      yAxis: Object.assign({ type: 'category', data: premium.map(function (p) { return p.orientation; }) }, axisStyle(), { splitLine: { show: false } }),
      series: [
        {
          type: 'bar', barWidth: '48%',
          data: values,
          itemStyle: { color: function (p) { return p.value >= 0 ? COLORS.accent : COLORS.coral; }, borderRadius: 4 },
          markLine: { silent: true, symbol: 'none', data: [{ xAxis: 0 }], lineStyle: { color: COLORS.muted, type: 'dashed' } }
        },
        {
          type: 'custom',
          renderItem: function (params, api) {
            var idx = api.value(0);
            var a = api.coord([api.value(1), idx]);
            var b = api.coord([api.value(2), idx]);
            var y = a[1];
            return { type: 'group', children: [
              { type: 'line', shape: { x1: a[0], y1: y, x2: b[0], y2: y }, style: { stroke: COLORS.text, lineWidth: 1.4 } },
              { type: 'line', shape: { x1: a[0], y1: y - 4, x2: a[0], y2: y + 4 }, style: { stroke: COLORS.text, lineWidth: 1.4 } },
              { type: 'line', shape: { x1: b[0], y1: y - 4, x2: b[0], y2: y + 4 }, style: { stroke: COLORS.text, lineWidth: 1.4 } }
            ] };
          },
          encode: { x: [1, 2], y: 0 },
          data: premium.map(function (p, i) { return [i, (p.premium_ci_low || 0) * 100, (p.premium_ci_high || 0) * 100]; })
        }
      ]
    });

    var district = data.district_premium || [];
    draw('chartDistrictPremium', {
      tooltip: { trigger: 'axis', formatter: function (ps) { var d = district[ps[0].dataIndex]; return d.district + '<br/>溢价 ' + (d.premium * 100).toFixed(1) + '%<br/>p = ' + Number(d.p_value).toFixed(3); } },
      grid: { left: 56, right: 18, top: 10, bottom: 26 },
      xAxis: Object.assign({ type: 'value', axisLabel: { formatter: '{value}%', fontSize: 10, color: COLORS.muted } }, axisStyle()),
      yAxis: Object.assign({ type: 'category', data: district.map(function (d) { return d.district; }) }, axisStyle(), { splitLine: { show: false } }),
      series: [{
        type: 'bar', barWidth: '55%',
        data: district.map(function (d) { return (d.premium || 0) * 100; }),
        itemStyle: { color: function (p) { return p.value >= 0 ? COLORS.accent : COLORS.coral; }, borderRadius: 4 }
      }]
    });

    var box = data.boxplot || {};
    var names = Object.keys(box);
    draw('chartOrientationBox', {
      tooltip: { trigger: 'item' },
      grid: { left: 48, right: 16, top: 12, bottom: 26 },
      xAxis: Object.assign({ type: 'category', data: names }, axisStyle(), { splitLine: { show: false } }),
      yAxis: Object.assign({ type: 'value', name: '元/㎡', nameTextStyle: { fontSize: 10, color: COLORS.muted } }, axisStyle()),
      series: [{
        type: 'boxplot', data: names.map(function (n) { return box[n]; }),
        itemStyle: { color: COLORS.accentSoft, borderColor: COLORS.accent, borderWidth: 1.5 }
      }]
    });

    var kw = data.kruskal_wallis || {};
    $('#noticeTitle').textContent = '控制混杂后，朝南溢价未达显著';
    $('#noticeBody').textContent = '原始朝向单价分布差异显著（Kruskal-Wallis p=' +
      Number(kw.p_value || 0).toExponential(1) + '），但控制面积、楼层、户型、区县后，朝南相对朝北的 95% 置信区间跨 0。' +
      '观察性数据仅作相关估计，不做因果断言。' + (data.limitation || '');
  }

  /* ---------- 04 模型 ---------- */
  function renderModel(data) {
    $('#kpiBest').textContent = data.best_model === 'hist_gb' ? 'HistGB' : (data.best_model || '—');
    $('#kpiRmse').textContent = fmt(Math.round(data.best_test_rmse_yuan));
    $('#kpiR2').textContent = Number(data.best_test_r2_yuan || 0).toFixed(3);
    $('#kpiDrop').textContent = ((data.rmse_reduction_vs_district || 0) * 100).toFixed(1) + '%';

    var models = data.models || [];
    var barColors = { hist_gb: COLORS.accent, random_forest: COLORS.blue, ridge: COLORS.sand, baseline_district_mean: COLORS.coral };
    draw('chartModels', {
      tooltip: { trigger: 'axis', formatter: function (ps) { var m = models[ps[0].dataIndex]; return m.model + '<br/>RMSE ' + fmt(Math.round(m.rmse_yuan)) + ' 元/㎡<br/>R² ' + Number(m.r2_yuan).toFixed(3); } },
      grid: { left: 130, right: 20, top: 10, bottom: 26 },
      xAxis: Object.assign({ type: 'value' }, axisStyle()),
      yAxis: Object.assign({ type: 'category', data: models.map(function (m) { return m.model; }) }, axisStyle(), { splitLine: { show: false }, axisLabel: { fontSize: 9, color: COLORS.muted } }),
      series: [{
        type: 'bar', barWidth: '56%',
        data: models.map(function (m) { return Math.round(m.rmse_yuan); }),
        itemStyle: { color: function (p) { return barColors[models[p.dataIndex].model] || COLORS.accent; }, borderRadius: [0, 5, 5, 0] }
      }]
    });

    var ablation = data.ablation || [];
    draw('chartAblation', {
      tooltip: { trigger: 'axis' },
      legend: { top: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 10 } },
      grid: { left: 44, right: 18, top: 34, bottom: 30 },
      xAxis: Object.assign({ type: 'category', data: ablation.map(function (a) { return a.experiment; }), axisLabel: { fontSize: 9, color: COLORS.muted } }, axisStyle(), { splitLine: { show: false } }),
      yAxis: Object.assign({ type: 'value' }, axisStyle()),
      series: [
        { name: 'R²', type: 'bar', data: ablation.map(function (a) { return Number(a.r2_yuan.toFixed(3)); }), itemStyle: { color: COLORS.accent, borderRadius: [4, 4, 0, 0] }, barWidth: '30%' },
        { name: 'RMSE / 1万', type: 'bar', data: ablation.map(function (a) { return Number((a.rmse_yuan / 10000).toFixed(2)); }), itemStyle: { color: COLORS.coral, borderRadius: [4, 4, 0, 0] }, barWidth: '30%' }
      ]
    });

    var sensitivity = data.sensitivity || [];
    draw('chartSensitivity', {
      tooltip: { trigger: 'axis' },
      grid: { left: 100, right: 20, top: 10, bottom: 26 },
      xAxis: Object.assign({ type: 'value' }, axisStyle()),
      yAxis: Object.assign({ type: 'category', data: sensitivity.map(function (s) { return s.scenario; }) }, axisStyle(), { splitLine: { show: false }, axisLabel: { fontSize: 9, color: COLORS.muted } }),
      series: [{
        type: 'bar', barWidth: '54%',
        data: sensitivity.map(function (s) { return Math.round(s.rmse_yuan); }),
        itemStyle: { color: COLORS.accent, borderRadius: [0, 5, 5, 0] }
      }]
    });
  }

  function renderImportance(data) {
    var items = (data.features || []).slice(0, 12).reverse();
    draw('chartImportance', {
      tooltip: { trigger: 'axis' },
      grid: { left: 140, right: 22, top: 10, bottom: 26 },
      xAxis: Object.assign({ type: 'value' }, axisStyle()),
      yAxis: Object.assign({ type: 'category', data: items.map(function (f) { return f.feature; }) }, axisStyle(), { splitLine: { show: false }, axisLabel: { fontSize: 9, color: COLORS.muted } }),
      series: [{
        type: 'bar', barWidth: '58%',
        data: items.map(function (f) { return Number(f.importance_mean.toFixed(4)); }),
        itemStyle: { color: COLORS.accent, borderRadius: [0, 5, 5, 0] }
      }]
    });
  }

  /* ---------- 05 预测 ---------- */
  var modelConfig = null;

  function fillSelects() {
    if (!modelConfig) return;
    var district = $('#inDistrict');
    district.innerHTML = modelConfig.districts.map(function (d) {
      return '<option value="' + d + '"' + (d === modelConfig.default_inputs.district ? ' selected' : '') + '>' + d + '</option>';
    }).join('');
    var orientation = $('#inOrientation');
    orientation.innerHTML = modelConfig.orientation_options.map(function (o) {
      return '<option value="' + o + '"' + (o === modelConfig.default_inputs.orientation ? ' selected' : '') + '>' + o + '</option>';
    }).join('');
    var floor = $('#inFloor');
    floor.innerHTML = modelConfig.floor_options.map(function (f) {
      return '<option value="' + f + '"' + (f === modelConfig.default_inputs.floor_level ? ' selected' : '') + '>' + f + '</option>';
    }).join('');
  }

  // 与 models/predict.py 的 build_feature_vector 保持一致。
  function buildVector(inputs) {
    var values = Object.assign({}, modelConfig.defaults);
    var area = Number(inputs.area);
    values.area = area;
    values.log_area = Math.log(area);
    ['rooms', 'halls', 'total_floors'].forEach(function (key) {
      if (inputs[key] !== '' && inputs[key] !== null && inputs[key] !== undefined && key in values) {
        values[key] = Number(inputs[key]);
      }
    });
    if ('rooms_missing' in values) values.rooms_missing = inputs.rooms === '' ? 1 : 0;
    values.floor_ratio = modelConfig.floor_ratio[inputs.floor_level];
    if ('floor_level_code' in values) values.floor_level_code = modelConfig.floor_code[inputs.floor_level];
    if ('floor_missing' in values) values.floor_missing = 0;

    var mapped = modelConfig.orientation_inputs[inputs.orientation];
    var main = mapped[0];
    if ('is_south' in values) values.is_south = mapped[1];
    if ('is_south_north' in values) values.is_south_north = mapped[2];
    if ('orientation_count' in values) values.orientation_count = mapped[3];
    if ('orientation_missing' in values) values.orientation_missing = main === '未知' ? 1 : 0;
    if ('orientation_score' in values) values.orientation_score = modelConfig.orientation_score[main];
    if ('south_ratio' in values) values.south_ratio = mapped[4];
    modelConfig.orientations.forEach(function (o) { values['orientation_main=' + o] = 0; });
    if ('orientation_main=' + main in values) values['orientation_main=' + main] = 1;

    modelConfig.districts.forEach(function (d) { values['district=' + d] = 0; });
    if ('district=' + inputs.district in values) values['district=' + inputs.district] = 1;
    var buildingType = inputs.building_type || (modelConfig.default_inputs || {}).building_type || '未知';
    modelConfig.building_types.forEach(function (b) { values['building_type=' + b] = 0; });
    if ('building_type=' + buildingType in values) values['building_type=' + buildingType] = 1;
    return modelConfig.features.map(function (f) { return Number(values[f]) || 0; });
  }

  function countUp(el, target) {
    var start = performance.now();
    var duration = 800;
    function frame(now) {
      var t = Math.min((now - start) / duration, 1);
      var eased = 1 - Math.pow(1 - t, 3);
      el.textContent = fmt(Math.round(target * eased));
      if (t < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  function bindPredict() {
    var form = $('#predictForm');
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      var inputs = Object.fromEntries(new FormData(form));
      var button = $('#predictBtn');
      button.disabled = true;
      $('#resultCaption').textContent = '正在估算…';
      try {
        var price;
        if (CFG.mode === 'local') {
          var response = await CFG.predict(inputs);
          price = response.predicted_unit_price;
        } else {
          var vector = buildVector(inputs);
          var logPrediction = modelConfig.intercept + vector.reduce(function (sum, value, i) {
            return sum + value * modelConfig.coefficients[i];
          }, 0);
          price = Math.exp(logPrediction);
        }
        countUp($('#resultValue'), price);
        $('#resultCaption').textContent = inputs.district + ' · ' + inputs.area + '㎡ · ' + inputs.orientation + ' · ' + inputs.floor_level + '楼层';
        $('#resultChip').textContent = CFG.mode === 'local' ? 'FastAPI · Ridge' : '静态演示 · Ridge 系数';
      } catch (error) {
        $('#resultValue').textContent = '—';
        $('#resultCaption').textContent = error.message || '预测失败';
      } finally {
        button.disabled = false;
      }
    });
  }

  /* ---------- 磁吸交互 ---------- */
  function magnetize(el) {
    el.addEventListener('pointermove', function (e) {
      if (e.pointerType === 'touch') return;
      var rect = el.getBoundingClientRect();
      var dx = (e.clientX - rect.left - rect.width / 2) * 0.18;
      var dy = (e.clientY - rect.top - rect.height / 2) * 0.22;
      el.classList.remove('is-snapping');
      el.style.transform = 'translate(' + dx.toFixed(1) + 'px,' + dy.toFixed(1) + 'px)';
    });
    el.addEventListener('pointerleave', function () {
      el.classList.add('is-snapping');
      el.style.transform = 'translate(0,0)';
      window.setTimeout(function () { el.classList.remove('is-snapping'); }, 520);
    });
  }

  function moveIndicator(button) {
    var indicator = $('#navIndicator');
    if (!indicator || !button) return;
    indicator.style.left = button.offsetLeft + 'px';
    indicator.style.width = button.offsetWidth + 'px';
  }

  function bindNavigation() {
    var buttons = $$('.nav-btn');
    buttons.forEach(function (button) {
      magnetize(button);
      button.addEventListener('click', function () {
        buttons.forEach(function (b) { b.classList.toggle('is-active', b === button); });
        moveIndicator(button);
        var target = document.getElementById(button.dataset.target);
        if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });
    $$('.primary-btn').forEach(magnetize);

    moveIndicator(buttons[0]);
    var stage = $('#stage');
    stage.addEventListener('scroll', function () {
      var sections = $$('.section');
      var stageTop = stage.getBoundingClientRect().top;
      var best = null;
      var bestDistance = Infinity;
      sections.forEach(function (section) {
        var distance = Math.abs(section.getBoundingClientRect().top - stageTop);
        if (distance < bestDistance) { bestDistance = distance; best = section; }
      });
      if (best) {
        var active = buttons.filter(function (b) { return b.dataset.target === best.id; })[0];
        if (active && !active.classList.contains('is-active')) {
          buttons.forEach(function (b) { b.classList.toggle('is-active', b === active); });
          moveIndicator(active);
        }
      }
    }, { passive: true });
  }

  function bindReveal() {
    var stage = $('#stage');
    if (!('IntersectionObserver' in window)) {
      $$('.section').forEach(function (s) { s.classList.add('is-visible'); });
      return;
    }
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) entry.target.classList.add('is-visible');
      });
    }, { root: stage, threshold: 0.08 });
    $$('.section').forEach(function (s) { observer.observe(s); });
  }

  /* ---------- 启动 ---------- */
  async function init() {
    $('#modeText').textContent = CFG.mode === 'local' ? '本地动态版' : '静态演示版';
    $('#footerStatus').textContent = CFG.mode === 'local' ? '数据来自 FastAPI /api' : '数据来自预导出 JSON';
    bindNavigation();
    bindReveal();
    bindPredict();

    try { modelConfig = await CFG.load('static_model'); fillSelects(); }
    catch (error) { $('#predictTag').textContent = '模型不可用'; }

    var tasks = [
      ['overview', renderOverview],
      ['preprocessing', renderPreprocessing],
      ['orientation', renderOrientation],
      ['metrics', renderModel],
      ['importance', renderImportance]
    ];
    await Promise.all(tasks.map(async function (task) {
      try { task[1](await CFG.load(task[0])); }
      catch (error) {
        var target = { overview: '#chartSources', preprocessing: '#chartSteps', orientation: '#chartPremium', metrics: '#chartModels', importance: '#chartImportance' }[task[0]];
        var el = document.querySelector(target);
        if (el) el.innerHTML = '<div style="padding:28px;text-align:center;color:#8293a0;font-size:12px">' + (error.message || '暂无数据') + '</div>';
      }
    }));

    window.addEventListener('resize', function () { charts.forEach(function (c) { c.resize(); }); });
    window.setTimeout(function () { moveIndicator($('.nav-btn.is-active')); }, 120);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
