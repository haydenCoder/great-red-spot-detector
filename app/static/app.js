(() => {
  const $ = (id) => document.getElementById(id);
  let filePath = null;
  let winjuposPath = null;
  let lastLogId = 0;
  let lastHandledJobId = null;
  let wasRunning = false;
  let userTab = "preview";
  let lastResult = null;
  let drawerOpen = false;
  let countries = {};
  let syncingTime = false;

  const nowStamp = () => {
    const d = new Date(), p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  };
  const esc = (s) => String(s).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
  const isMobileLayout = () => window.matchMedia("(max-width: 900px)").matches;
  const pad2 = (n) => String(n).padStart(2, "0");

  const setStatus = (k, t) => {
    const el = $("statusPill");
    if (!el) return;
    el.className = "pill " + k;
    el.textContent = t;
  };
  const numOrNull = (id) => {
    const el = $(id);
    if (!el || el.value === "" || el.value == null) return null;
    const v = parseFloat(el.value);
    return Number.isFinite(v) ? v : null;
  };
  const fmt = (v, d = 4) => (v == null || v === "" || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(d));

  function setModeBadge(kind, label) {
    const el = $("modeBadge");
    if (!el) return;
    el.className = "mode-badge " + (kind || "idle");
    el.textContent = label || "NO DATA YET";
  }

  function setPreviewSource(kind, label) {
    const el = $("previewSource");
    if (!el) return;
    el.className = "preview-source " + (kind || "none");
    el.textContent = label || "No image";
  }

  function setDrawer(open) {
    drawerOpen = !!open;
    const panel = $("controlsPanel");
    const backdrop = $("drawerBackdrop");
    const btn = $("menuToggle");
    if (panel) panel.classList.toggle("open", drawerOpen);
    if (backdrop) {
      backdrop.hidden = !drawerOpen;
      backdrop.classList.toggle("show", drawerOpen);
    }
    document.body.classList.toggle("drawer-open", drawerOpen && isMobileLayout());
    if (btn) {
      btn.setAttribute("aria-expanded", drawerOpen ? "true" : "false");
      btn.setAttribute("aria-label", drawerOpen ? "Close controls menu" : "Open controls menu");
      btn.textContent = drawerOpen ? "✕" : "☰";
    }
  }

  function showTab(name, force = false) {
    document.querySelectorAll(".tab").forEach((b) => {
      const on = b.dataset.tab === name;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    document.querySelectorAll(".tabpane").forEach((p) => {
      p.classList.toggle("active", p.id === "tab-" + name);
    });
    if (force) userTab = name;
  }

  function setBusy(busy) {
    ["btnProcess", "btnSynth", "btnFactory", "btnMulti", "btnHard", "btnEph", "btnNnTrain", "btnWjTemplate"].forEach((id) => {
      const el = $(id);
      if (!el) return;
      if (id === "btnProcess") el.disabled = busy || !filePath;
      else el.disabled = busy;
    });
  }

  function setText(id, text) {
    const el = $(id);
    if (el) el.textContent = text;
  }

  function appendLogs(lines) {
    const box = $("console");
    if (!box || !lines?.length) return;
    for (const ln of lines) {
      lastLogId = Math.max(lastLogId, ln.id);
      const div = document.createElement("div");
      div.className = "line " + (ln.level || "INFO");
      div.innerHTML = `<span class="ts">[${ln.ts}]</span><strong>${ln.level}</strong> ${esc(ln.msg)}`;
      box.appendChild(div);
    }
    box.scrollTop = box.scrollHeight;
    setText("logCount", box.children.length + " lines");
  }

  function renderBudget(components) {
    const host = $("budgetBars");
    if (!host) return;
    host.innerHTML = "";
    if (!components || typeof components !== "object") {
      host.innerHTML = '<div class="muted small">No formal error budget yet.</div>';
      return;
    }
    const entries = Object.entries(components).filter(([k]) => k !== "total");
    const total = Number(components.total) || Math.max(...entries.map(([, v]) => Number(v) || 0), 0.01);
    entries.forEach(([k, v]) => {
      const val = Number(v) || 0;
      const pct = Math.min(100, (val / total) * 100);
      const row = document.createElement("div");
      row.className = "budget-row";
      row.innerHTML = `<span class="nm">${esc(k)}</span><div class="bar"><div class="fill" style="width:${pct}%"></div></div><span class="vl">${val.toFixed(4)}″</span>`;
      host.appendChild(row);
    });
    if (components.total != null) {
      const row = document.createElement("div");
      row.className = "budget-row";
      row.innerHTML = `<span class="nm"><strong>total</strong></span><div class="bar"><div class="fill" style="width:100%;background:linear-gradient(90deg,#3dd68c,#3d9cf0)"></div></div><span class="vl"><strong>${Number(components.total).toFixed(4)}″</strong></span>`;
      host.appendChild(row);
    }
  }

  function sourceKindLabel(result) {
    const sk = result.source_kind || (result.headline && result.headline.source_kind) || "";
    if (result.kind === "factory_night") {
      if (sk === "real_file" || String(sk).includes("REAL")) return { kind: "real", label: "SELF-TEST · REAL FILE" };
      return { kind: "factory", label: "SELF-TEST · SYNTHETIC" };
    }
    if (sk === "real_file" || String(sk).includes("REAL") || result.kind === "process")
      return { kind: "real", label: "REAL FILE" };
    if (sk === "synthetic" || String(sk).includes("SYNTH") || result.truth || result.kind === "synthetic")
      return { kind: "synth", label: "SYNTHETIC TEST" };
    if (result.kind === "hard_synth" || result.calibration_grade) return { kind: "synth", label: "HARD SYNTH" };
    if (result.series || result.kind === "multi_epoch") return { kind: "multi", label: "MULTI-EPOCH" };
    return { kind: "idle", label: "RESULT" };
  }

  function updateDashboard(result) {
    lastResult = result;
    const h = result.headline || {};
    const stages = result.stages || {};
    const pe = result.pro_ephemeris || stages.ephemeris || result.ephemeris;
    const hard = stages.hard_synth || (result.calibration_grade ? result : null);
    const multi = stages.multi_epoch || (result.series ? result : null);
    const rg = result.research_grade || {};
    const tr = result.truth_recovery || (stages.measure && stages.measure.truth_recovery) || {};
    const src = sourceKindLabel(result);

    setModeBadge(src.kind, src.label);
    setText("dSource", src.label);
    setText("dRun", result.run_n != null ? String(result.run_n).padStart(4, "0") : (h.run_n != null ? String(h.run_n).padStart(4, "0") : "—"));
    setText("dOut", result.output_folder || result.output_dir || "—");

    setText("dStatus", result.kind === "factory_night" ? "Self-test DONE" : "DONE");
    if (h.synth_epoch || result.synth_epoch || (result.truth && result.truth.user_time_iso)) {
      const ep = h.synth_epoch || result.synth_epoch || result.truth.user_time_iso;
      const rnd = h.random_time ?? result.random_time ?? (result.truth && result.truth.random_time);
      setText("dStatus", (rnd ? "DONE · random epoch " : "DONE · epoch ") + ep);
    }
    setText("dGrade", h.measure_grade || h.grade || h.research_grade || rg.grade || "—");
    const gs = result.gold_standard || {};
    const sota = result.sota || {};
    setText(
      "dGsDef",
      h.sota_quality || sota.quality_grade || h.gold_primary_definition || gs.primary_definition || "—"
    );
    setText(
      "dLon",
      fmt(
        h.sota_lon_iii_deg ??
          sota.lon_iii_deg ??
          h.gold_lon_iii_deg ??
          gs.primary_lon_iii_deg ??
          h.lon_iii_deg_bias_corrected ??
          h.lon_iii_deg ??
          h.lon ??
          rg.lon_bias_corrected_deg,
        4
      ) + "°"
    );
    setText(
      "dLat",
      fmt(
        h.sota_lat_deg ??
          sota.lat_deg ??
          h.gold_lat_deg ??
          gs.primary_lat_deg ??
          h.lat_deg_bias_corrected ??
          h.lat_deg ??
          h.lat ??
          rg.lat_bias_corrected_deg,
        4
      ) + "°"
    );
    const wjSky = h.vs_winjupos_sky_arcsec ?? (gs.winjupos_manual && gs.winjupos_manual.sky_error_arcsec);
    const wjDlon = h.vs_winjupos_dlon_deg ?? (gs.winjupos_manual && gs.winjupos_manual.delta_lon_deg);
    setText(
      "dWj",
      wjSky != null
        ? `${fmt(wjSky, 3)}″ (Δlon ${fmt(wjDlon, 3)}°)`
        : "— (paste WJ manual)"
    );
    setText("dSigma", fmt(h.sigma_total_sky_arcsec ?? rg.sigma_total_sky_arcsec, 4) + "″");
    renderGold(result);
    const skyT = h.truth_recovery_sky_arcsec ?? tr.sky_error_arcsec;
    const isReal = src.kind === "real";
    setText(
      "dTruth",
      skyT != null
        ? `${fmt(skyT, 4)}″ (${tr.grade || h.truth_recovery_grade || "—"})`
        : isReal
          ? "— (real data; no synthetic truth)"
          : "—"
    );
    setText("dCm", h.cm_source || (pe && pe.cm_source) || "—");
    setText(
      "dDrift",
      h.drift_lon_deg_per_day != null
        ? fmt(h.drift_lon_deg_per_day, 4)
        : multi && multi.drift_lon_deg_per_day != null
          ? fmt(multi.drift_lon_deg_per_day, 4)
          : "—"
    );
    setText(
      "dCal",
      h.calibration_grade || (hard && hard.calibration_grade) || result.calibration_grade || "—"
    );

    const bits = [];
    bits.push(src.label);
    if (result.run_n != null) bits.push("run#" + String(result.run_n).padStart(4, "0"));
    if (h.metrology) bits.push(h.metrology);
    if (h.grade || rg.grade) bits.push("grade=" + (h.grade || rg.grade));
    if (skyT != null) bits.push(`truth_rec=${Number(skyT).toFixed(4)}″`);
    if (h.hard_coverage_2sigma != null) bits.push(`2σ_cov=${(100 * h.hard_coverage_2sigma).toFixed(0)}%`);
    setText("dHead", bits.join(" · ") || JSON.stringify(h).slice(0, 200));

    let comps = null;
    const methods = rg.methods || {};
    if (methods.error_budget && methods.error_budget.components_sky_arcsec) {
      comps = methods.error_budget.components_sky_arcsec;
    } else if (methods.vlbi_full && methods.vlbi_full.error_budget) {
      comps = methods.vlbi_full.error_budget.components_sky_arcsec;
    }
    renderBudget(comps);

    if (pe) {
      setText("ephBox", JSON.stringify(pe, null, 2));
      setText(
        "ephSummary",
        `CM III=${fmt(pe.cm_iii_deg, 4)}° [${pe.cm_source}]  Δ=${fmt(pe.distance_au, 5)} AU  ` +
          `sublat=${fmt(pe.sub_obs_lat_deg, 3)}°  PA=${fmt(pe.north_pa_deg, 2)}°  ori=${pe.apply_orientation}`
      );
    }
    if (multi && (multi.series || multi.points || multi.n_epochs != null)) {
      setText("multiBox", JSON.stringify(multi.series || multi, null, 2));
    }
    if (hard && (hard.overall || hard.calibration_grade || result.results)) {
      setText(
        "hardBox",
        JSON.stringify(
          hard.overall
            ? hard
            : {
                calibration_grade: result.calibration_grade,
                overall: result.overall,
                by_family: result.by_family,
                results: result.results,
              },
          null,
          2
        )
      );
    }
    if (result.nasa) setText("nasaBox", JSON.stringify(result.nasa, null, 2));
  }

  function showPreview(url, label, kind) {
    const img = $("previewImg");
    const empty = $("previewEmpty");
    if (url && img) {
      const full = url + (url.includes("?") ? "&" : "?") + "t=" + Date.now();
      if (img.dataset.src !== full) {
        img.src = full;
        img.dataset.src = full;
      }
      img.classList.add("show");
      if (empty) empty.style.display = "none";
      setPreviewSource(kind || "real", label || "Image");
    }
  }

  function renderGold(result) {
    const host = $("goldBox");
    if (!host) return;
    const gs = result.gold_standard;
    const sota = result.sota || {};
    if ((!gs || !gs.ok) && !sota.ok) {
      host.innerHTML =
        '<div class="muted small">Multi-method list not available yet. Run Process or a synthetic test.</div>';
      return;
    }
    const f4 = (v) => (v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(4));
    let rows = ((gs && gs.measures) || [])
      .filter((m) => m.ok !== false && m.definition_id && Number.isFinite(Number(m.lon_iii_deg)))
      .slice(0, 60)
      .map(
        (m) =>
          `<tr><td>${esc(m.definition_id)}</td><td class="mono you">${f4(m.lon_iii_deg)}°</td><td class="mono">${f4(m.lat_deg)}°</td></tr>`
      )
      .join("");
    let wj = "";
    if (gs && gs.winjupos_manual) {
      const w = gs.winjupos_manual;
      wj = `<div class="nasa-plain"><strong>vs your WinJUPOS manual:</strong> Δlon=${f4(w.delta_lon_deg)}°  Δlat=${f4(w.delta_lat_deg)}°  sky=${f4(w.sky_error_arcsec)}″ — ${esc(w.agreement || "")}</div>`;
    }
    const nOk = (gs && gs.n_methods_ok) ?? result.headline?.n_methods_ok ?? sota.n_methods_ok;
    const nTot = (gs && gs.n_methods_total) ?? result.headline?.n_methods_total ?? sota.n_methods_total;
    const nLabel = nOk != null ? ` · methods ${nOk}/${nTot || "?"} ok` : "";
    const sotaBlock = sota.ok
      ? `<div class="nasa-plain" style="margin-bottom:8px;border:1px solid #1d4a36;padding:8px;border-radius:8px;background:#0c1a14">
          <strong>Multi-method consensus (scatter):</strong>
          <span class="mono you">${f4(sota.lon_iii_deg)}°</span> /
          <span class="mono you">${f4(sota.lat_deg)}°</span>
          · ${esc(sota.quality_grade || "—")} score=${esc(String(sota.quality_score ?? "—"))}
          · inliers ${sota.n_inliers}/${(sota.n_inliers || 0) + (sota.n_outliers || 0)}
          · σ_lon ${f4(sota.sigma_lon_deg)}°
          ${sota.outlier_ids && sota.outlier_ids.length ? `<div class="muted small">Excluded: ${esc(sota.outlier_ids.slice(0, 14).join(", "))}${sota.outlier_ids.length > 14 ? "…" : ""}</div>` : ""}
          <div class="muted small">For the number to report, open the publish / best-answer card in the job folder.</div>
        </div>`
      : "";
    const ah = result.ai_hard_case || {};
    const aiBlock = ah.difficulty != null
      ? `<div class="nasa-plain" style="margin-bottom:8px;border:1px solid ${ah.nn_used ? "#6b3fa0" : "#2a3548"};padding:8px;border-radius:8px">
          <strong>Hard-frame assist:</strong> difficulty=${f4(ah.difficulty)}
          · engaged=${ah.engaged ? "yes" : "no"}
          · CNN=${ah.nn_used ? `yes w=${f4(ah.blend_weight)}` : "no"}
          <div class="muted small">${esc(ah.note || "")}</div>
        </div>`
      : "";
    host.innerHTML = `
      ${sotaBlock}
      ${aiBlock}
      <div class="nasa-plain">
        <strong>Named definition:</strong> ${esc((gs && gs.primary_definition) || "—")} ·
        <span class="mono you">${f4(gs && gs.primary_lon_iii_deg)}°</span> /
        <span class="mono you">${f4(gs && gs.primary_lat_deg)}°</span> ·
        CM=${esc((gs && gs.cm_source) || "—")}${nLabel}
      </div>
      <div class="nasa-table-wrap" style="margin-top:8px">
        <table class="nasa-table">
          <thead><tr><th>Method</th><th>Lon III</th><th>Lat</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      ${wj}
      <div class="muted small" style="margin-top:6px">Consensus clips outliers. Planet geometry from Horizons is not a GRS catalogue.</div>`;
  }

  function renderNasaCompare(result) {
    const host = $("nasaCompareBox");
    if (!host) return;
    const nasa = result.nasa;
    if (!nasa || !nasa.measured) {
      host.innerHTML =
        '<div class="muted small">No geometry compare on this job. Optional sanity only.</div>';
      return;
    }
    const m = nasa.measured || {};
    const r = nasa.reference || {};
    const d = nasa.deltas || {};
    const f4 = (v) => (v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(4));
    const signed = (v) => {
      if (v == null || Number.isNaN(Number(v))) return "—";
      const n = Number(v);
      return (n >= 0 ? "+" : "") + n.toFixed(4);
    };
    host.innerHTML = `
      <div class="nasa-table-wrap">
        <table class="nasa-table">
          <thead>
            <tr><th>Quantity</th><th>YOUR measure</th><th>Model / Horizons ctx</th><th>Δ (context only)</th></tr>
          </thead>
          <tbody>
            <tr>
              <td>Longitude III (°)</td>
              <td class="mono you">${f4(m.lon_iii_deg)}</td>
              <td class="mono nasa">${f4(r.lon_iii_deg)}</td>
              <td class="mono delta">${signed(d.lon_iii_deg)}</td>
            </tr>
            <tr>
              <td>Latitude (°)</td>
              <td class="mono you">${f4(m.lat_deg)}</td>
              <td class="mono nasa">${f4(r.lat_deg)}</td>
              <td class="mono delta">${signed(d.lat_deg)}</td>
            </tr>
            <tr>
              <td>Length (°)</td>
              <td class="mono you">${f4(m.length_deg)}</td>
              <td class="mono nasa">${f4(r.length_deg)}</td>
              <td class="mono delta">${signed(d.length_deg)}</td>
            </tr>
            <tr>
              <td>Width (°)</td>
              <td class="mono you">${f4(m.width_deg)}</td>
              <td class="mono nasa">${f4(r.width_deg)}</td>
              <td class="mono delta">${signed(d.width_deg)}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="nasa-plain">
        <div><strong>Grade:</strong> ${esc(nasa.grade || "—")}</div>
        <div><strong>Source:</strong> ${esc(nasa.source || "—")}</div>
        <div class="muted small">Not a NASA GRS longitude. Prefer publish number + your WinJUPOS paste for a real check.</div>
      </div>`;
  }

  function renderDashboardTable(result) {
    const el = $("dashTable");
    if (!el) return;
    const h = result.headline || {};
    const pub = result.publish || {};
    const ch = result.champion || {};
    const pe = result.pro_ephemeris || {};
    const eq = (pub.winjupos_equality) || {};
    const dual = result.dual_measure || {};
    const lon = pub.publish_lon_iii_deg ?? h.publish_lon_iii_deg ?? h.lon_iii_deg;
    const lat = pub.publish_lat_deg ?? h.publish_lat_deg ?? h.lat_deg;
    const latg = pub.publish_lat_planetographic_deg ?? h.lat_planetographic_deg;
    const cm = pub.cm_iii_deg ?? h.cm_iii_deg ?? pe.cm_iii_deg;
    const cms = pub.cm_source ?? h.cm_source ?? pe.cm_source;
    const def = pub.publish_definition ?? h.publish_definition ?? h.primary_method;
    const grade = h.superduper_grade || ch.grade || h.champion_grade || h.grade || "—";
    const sig = pub.publish_sigma_sky_arcsec ?? h.champion_sigma_sky_arcsec ?? h.sigma_total_sky_arcsec;
    const ew = h.extent_ew_deg ?? ch.extent_ew_deg ?? h.length_deg;
    const utc = h.user_time || h.synth_epoch || "—";
    const f4 = (v) => (v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(4));
    const f3 = (v) => (v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(3));
    const f2 = (v) => (v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(2));
    const rows = [
      ["UTC", utc],
      ["lon_III °", f4(lon)],
      ["lat_c °", f3(lat)],
      ["lat_g °", f3(latg)],
      ["CM_III °", `${f4(cm)}  [${cms || "—"}]`],
      ["definition", def || "—"],
      ["grade", grade],
      ["σ_sky ″", f2(sig)],
      ["EW °", f2(ew)],
      ["vs_WJ", eq.agreement || h.winjupos_agreement || "—"],
      ["Δsky_WJ ″", f2(eq.sky_error_arcsec ?? h.vs_winjupos_sky_arcsec)],
      ["gates", `${h.ultimate_lock_pass ?? "—"}/${h.ultimate_lock_total ?? "—"}`],
    ];
    if (dual && (dual.automatic || dual.human)) {
      const a = dual.automatic || {};
      const hu = dual.human || {};
      const c = dual.comparison || {};
      rows.push(["dual", dual.official || "—"]);
      rows.push(["auto lon", f4(a.lon_iii_deg)]);
      rows.push(["hand lon", f4(hu.lon_iii_deg)]);
      rows.push(["Δsky dual ″", `${f2(c.sky_delta_arcsec)}  (${c.agreement || "—"})`]);
    }
    let w1 = 10, w2 = 12;
    rows.forEach(([a, b]) => {
      w1 = Math.max(w1, String(a).length);
      w2 = Math.max(w2, Math.min(48, String(b).length));
    });
    const bar = `+-${"-".repeat(w1)}-+-${"-".repeat(w2)}-+`;
    const lines = ["DASHBOARD", bar, `| ${"field".padEnd(w1)} | ${"value".padEnd(w2)} |`, bar];
    rows.forEach(([a, b]) => {
      lines.push(`| ${String(a).padEnd(w1)} | ${String(b).slice(0, w2).padEnd(w2)} |`);
    });
    lines.push(bar);
    el.textContent = lines.join("\n");
  }

  function renderResult(result) {
    updateDashboard(result);
    renderDashboardTable(result);
    renderNasaCompare(result);
    const h = result.headline;
    const src = sourceKindLabel(result);

    // Full report tab: prefer long text, else full JSON dump
    if (result.text && String(result.text).length > 80) {
      setText("resultsBox", result.text);
    } else {
      let text = "";
      text += `=== SOURCE: ${src.label} ===\n`;
      if (result.run_n != null) text += `run_n: ${result.run_n}\n`;
      if (result.output_folder) text += `folder: ${result.output_folder}\n\n`;

      // Always surface YOUR answer + NASA deltas even without full report builder
      const lon =
        (h && (h.lon_iii_deg_bias_corrected ?? h.lon_iii_deg ?? h.lon)) ??
        (result.research_grade && result.research_grade.lon_bias_corrected_deg);
      const lat =
        (h && (h.lat_deg_bias_corrected ?? h.lat_deg ?? h.lat)) ??
        (result.research_grade && result.research_grade.lat_bias_corrected_deg);
      text += "=== YOUR ANSWER ===\n";
      text += `  LON_III_DEG = ${lon != null ? Number(lon).toFixed(6) : "—"}\n`;
      text += `  LAT_DEG     = ${lat != null ? Number(lat).toFixed(6) : "—"}\n`;
      text += `  GRADE       = ${(h && (h.grade || h.measure_grade)) || (result.research_grade && result.research_grade.grade) || "—"}\n\n`;

      if (result.nasa && result.nasa.measured) {
        const m = result.nasa.measured;
        const r = result.nasa.reference || {};
        const d = result.nasa.deltas || {};
        text += "=== NASA COMPARE ===\n";
        text += `  YOUR  lon=${m.lon_iii_deg}  lat=${m.lat_deg}\n`;
        text += `  NASA  lon=${r.lon_iii_deg}  lat=${r.lat_deg}\n`;
        text += `  Δ     lon=${d.lon_iii_deg}  lat=${d.lat_deg}\n`;
        text += `  grade=${result.nasa.grade || "—"}\n\n`;
      }
      if (result.truth_recovery) {
        text += "=== TRUTH RECOVERY (synthetic) ===\n";
        text += JSON.stringify(result.truth_recovery, null, 2) + "\n\n";
      }
      if (result.kind === "factory_night") {
        text += "=== SELF-TEST HEADLINE ===\n" + JSON.stringify(h, null, 2) + "\n\n";
      }
      text += "=== FULL JSON ===\n" + JSON.stringify(result, null, 2);
      setText("resultsBox", text);
    }

    // NASA tab gets the same clear dump
    if (result.nasa) {
      let nt = "GEOMETRY CONTEXT (NOT NASA GRS TRUTH)\n\n";
      const m = result.nasa.measured || {};
      const r = result.nasa.reference || {};
      const d = result.nasa.deltas || {};
      nt += `YOUR  lon=${m.lon_iii_deg}  lat=${m.lat_deg}  L=${m.length_deg}  W=${m.width_deg}\n`;
      nt += `MODEL lon=${r.lon_iii_deg}  lat=${r.lat_deg}  L=${r.length_deg}  W=${r.width_deg}\n`;
      nt += `Δ     lon=${d.lon_iii_deg}  lat=${d.lat_deg}  L=${d.length_deg}  W=${d.width_deg}\n`;
      nt += `grade=${result.nasa.grade || "—"}  source=${result.nasa.source || "—"}\n`;
      nt += `disclaimer=${result.nasa.disclaimer || result.nasa.note || "geometry context only"}\n\n`;
      if (result.gold_standard) {
        nt += "=== GOLD STANDARD (pro procedure) ===\n";
        nt += JSON.stringify(result.gold_standard, null, 2) + "\n\n";
      }
      nt += JSON.stringify(result.nasa, null, 2);
      setText("nasaBox", nt);
    }

    const url = result.preview || result.png;
    const label =
      result.preview_label ||
      (src.kind === "real"
        ? "Your uploaded file (real)"
        : src.kind === "synth"
          ? "SYNTHETIC (not a real photo)"
          : src.label);
    if (url) {
      showPreview(url, label, src.kind);
      setText("previewRun", result.run_n != null ? `run #${String(result.run_n).padStart(4, "0")}` : "");
    }
  }

  function countryOffsetHours() {
    const code = $("country") ? $("country").value : "UTC";
    const c = countries[code];
    return c && Number.isFinite(c.utc_offset_h) ? Number(c.utc_offset_h) : 0;
  }

  function updateCountryHint() {
    const code = $("country") ? $("country").value : "UTC";
    const c = countries[code];
    const el = $("countryHint");
    if (!el) return;
    if (c) {
      el.textContent = `${c.name}: ${c.hint}. Ephemeris & Process use UTC mid-exposure.`;
    } else {
      el.textContent = "Pick country so local clock vs UTC is clear. Ephemeris uses UTC.";
    }
  }

  function minutesToHHMMSS(mins) {
    const m = Math.max(0, Math.min(1439, Math.round(mins)));
    const h = Math.floor(m / 60);
    const mm = m % 60;
    return `${pad2(h)}:${pad2(mm)}:00`;
  }

  function parseUserTimeParts() {
    const s = ($("userTime") && $("userTime").value.trim()) || "";
    const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
    if (!m) return null;
    return {
      y: +m[1], mo: +m[2], d: +m[3],
      h: +m[4], mi: +m[5], s: m[6] != null ? +m[6] : 0,
    };
  }

  function applyTimeBarToUserTime() {
    if (syncingTime) return;
    syncingTime = true;
    try {
      const bar = $("timeBar");
      const dateEl = $("obsDate");
      if (!bar || !dateEl) return;
      const mins = parseInt(bar.value, 10) || 0;
      const hhmmss = minutesToHHMMSS(mins);
      setText("timeBarReadout", hhmmss);
      const date = dateEl.value || nowStamp().slice(0, 10);
      // Bar is "local-ish" using country offset: store UTC in userTime
      const [hh, mm] = hhmmss.split(":").map(Number);
      const localMins = hh * 60 + mm;
      const utcMinsTotal = localMins - countryOffsetHours() * 60;
      let dayShift = 0;
      let um = utcMinsTotal;
      if (um < 0) {
        um += 1440;
        dayShift = -1;
      } else if (um >= 1440) {
        um -= 1440;
        dayShift = 1;
      }
      const uh = Math.floor(um / 60);
      const umi = um % 60;
      const d0 = new Date(`${date}T12:00:00Z`);
      d0.setUTCDate(d0.getUTCDate() + dayShift);
      const y = d0.getUTCFullYear();
      const mo = pad2(d0.getUTCMonth() + 1);
      const da = pad2(d0.getUTCDate());
      if ($("userTime")) {
        $("userTime").value = `${y}-${mo}-${da} ${pad2(uh)}:${pad2(umi)}:00`;
      }
    } finally {
      syncingTime = false;
    }
  }

  function applyUserTimeToBar() {
    if (syncingTime) return;
    syncingTime = true;
    try {
      const p = parseUserTimeParts();
      if (!p) return;
      // userTime is UTC; bar shows local = UTC + offset
      const utcMins = p.h * 60 + p.mi;
      let local = utcMins + countryOffsetHours() * 60;
      if (local < 0) local += 1440;
      if (local >= 1440) local -= 1440;
      if ($("timeBar")) $("timeBar").value = String(Math.round(local));
      setText("timeBarReadout", minutesToHHMMSS(local));
      if ($("obsDate")) {
        $("obsDate").value = `${p.y}-${pad2(p.mo)}-${pad2(p.d)}`;
      }
    } finally {
      syncingTime = false;
    }
  }

  function payload(extra = {}) {
    const user_time = $("userTime").value.trim();
    if (!user_time) {
      alert("Enter observation time YYYY-MM-DD HH:MM:SS (UTC mid-exposure of your real photo)");
      $("userTime").focus();
      return null;
    }
    const body = {
      user_time,
      country: $("country") ? $("country").value : "UTC",
      region: $("region") ? $("region").value : "global",
      time_error_seconds: parseFloat($("timeError").value || "0"),
      aperture_m: 0.35, // fixed default; aperture removed from UI
      verbose: $("verboseToggle").checked,
      nasa_compare: $("nasaCompare").checked,
      mc_iterations: parseInt($("mcIter").value || "50", 10),
      injection_trials: parseInt($("injectionN").value || "28", 10),
      max_fidelity: $("maxFidelity").checked,
      factory_mode: $("factoryMode").checked,
      use_vlbi: $("useVlbi").checked,
      use_nn: $("useNn").checked,
      use_horizons: $("useHorizons").checked,
      use_spice: $("useSpice").checked,
      resolution_preset: $("resolution").value,
      ...extra,
    };
    const cm = numOrNull("cmOverride");
    if (cm != null) body.cm_iii_override = cm;
    const sl = numOrNull("subLatOverride");
    if (sl != null) body.sub_lat_override = sl;
    const pa = numOrNull("northPaOverride");
    if (pa != null) body.north_pa_override = pa;
    if (winjuposPath) body.winjupos_path = winjuposPath;
    // Optional human WinJUPOS measure (validation — WJ does not auto-detect GRS)
    const wjLon = numOrNull("wjManualLon");
    const wjLat = numOrNull("wjManualLat");
    if (wjLon != null) body.winjupos_manual_lon = wjLon;
    if (wjLat != null) body.winjupos_manual_lat = wjLat;
    return body;
  }

  async function uploadFile(file) {
    const fd = new FormData();
    fd.append("file", file);
    const j = await (await fetch("/api/upload", { method: "POST", body: fd })).json();
    if (!j.ok) return alert(j.error || "upload failed");
    filePath = j.path;
    $("fileInfo").textContent = `REAL file loaded: ${j.original}`;
    $("fileInfo").classList.remove("muted");
    $("fileInfo").classList.add("file-real");
    $("btnProcess").disabled = false;
    setModeBadge("real", "REAL FILE LOADED");
    if (j.preview) {
      showPreview(j.preview, `Your file: ${j.original}`, "real");
      setText("previewRun", "upload preview");
      showTab("preview", true);
    }
  }

  async function uploadWinjupos(file) {
    const fd = new FormData();
    fd.append("file", file);
    const j = await (await fetch("/api/winjupos/upload", { method: "POST", body: fd })).json();
    if (!j.ok) return alert(j.error || "WinJUPOS upload failed");
    winjuposPath = j.path;
    $("winjuposInfo").textContent = `WinJUPOS CML: ${file.name} → ${j.path}`;
  }

  async function startJob(url, body, statusText) {
    if (!body) return;
    lastHandledJobId = null;
    wasRunning = true;
    setStatus("run", statusText || "RUNNING");
    setBusy(true);
    setText("dStatus", statusText || "Running…");
    if (isMobileLayout()) setDrawer(false);
    try {
      const j = await (
        await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
      ).json();
      if (!j.ok) {
        alert(j.error || "failed");
        setStatus("err", "ERROR");
        setBusy(false);
        wasRunning = false;
      }
    } catch (e) {
      alert(String(e));
      setStatus("err", "ERROR");
      setBusy(false);
      wasRunning = false;
    }
  }

  async function poll() {
    try {
      const j = await (await fetch(`/api/logs?after=${lastLogId}`)).json();
      if (j.lines?.length) appendLogs(j.lines);
    } catch (_) {}

    try {
      const j = await (await fetch("/api/job")).json();
      if (j.running) {
        wasRunning = true;
        setStatus("run", (j.kind || "RUN").toUpperCase());
        setBusy(true);
        return;
      }

      setBusy(false);

      if (j.error) {
        setStatus("err", "ERROR");
        if (lastHandledJobId !== "err:" + j.error) {
          setText("resultsBox", "Error:\n" + j.error);
          setText("dStatus", "Error");
          setText("dHead", j.error);
          lastHandledJobId = "err:" + j.error;
          showTab("results", true);
        }
        wasRunning = false;
        return;
      }

      if (j.result) {
        setStatus("ok", "DONE");
        const jid = j.result.job_id || JSON.stringify(j.result).slice(0, 40);
        if (lastHandledJobId !== jid) {
          lastHandledJobId = jid;
          renderResult(j.result);
          if (wasRunning) {
            if (j.result.kind === "factory_night") showTab("dashboard", true);
            else if (j.result.calibration_grade) showTab("hard", true);
            else if (j.result.series || (j.result.headline && j.result.headline.drift_lon_deg_per_day != null))
              showTab("multi", true);
            else showTab("dashboard", true);
          }
        }
        wasRunning = false;
        return;
      }

      if (!wasRunning) setStatus("idle", "IDLE");
      wasRunning = false;
    } catch (_) {}
  }

  // Mobile drawer
  $("menuToggle")?.addEventListener("click", () => setDrawer(!drawerOpen));
  $("drawerBackdrop")?.addEventListener("click", () => setDrawer(false));
  $("btnCloseDrawer")?.addEventListener("click", () => setDrawer(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && drawerOpen) setDrawer(false);
  });
  window.addEventListener(
    "resize",
    (() => {
      let t;
      return () => {
        clearTimeout(t);
        t = setTimeout(() => {
          if (!isMobileLayout() && drawerOpen) setDrawer(false);
        }, 120);
      };
    })()
  );

  // Tabs
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      userTab = btn.dataset.tab;
      showTab(userTab, true);
    });
  });

  function wireDrop(zoneId, inputId, onFile) {
    const zone = $(zoneId);
    const input = $(inputId);
    if (!zone || !input) return;
    const open = () => input.click();
    zone.addEventListener("click", open);
    zone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    });
    input.addEventListener("change", (e) => e.target.files[0] && onFile(e.target.files[0]));
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      zone.classList.add("drag");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("drag"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("drag");
      e.dataTransfer.files[0] && onFile(e.dataTransfer.files[0]);
    });
  }
  wireDrop("dropZone", "fileInput", uploadFile);
  wireDrop("winjuposDrop", "winjuposInput", uploadWinjupos);

  // Time bar wiring
  $("timeBar")?.addEventListener("input", applyTimeBarToUserTime);
  $("obsDate")?.addEventListener("change", applyTimeBarToUserTime);
  $("userTime")?.addEventListener("change", applyUserTimeToBar);
  $("userTime")?.addEventListener("blur", applyUserTimeToBar);
  $("country")?.addEventListener("change", () => {
    updateCountryHint();
    applyUserTimeToBar();
  });

  // Buttons
  $("btnProcess").addEventListener("click", () => {
    setModeBadge("real", "PROCESSING REAL…");
    startJob("/api/process", payload({ path: filePath }), "PROCESS REAL");
  });
  $("btnSynth").addEventListener("click", () => {
    setModeBadge("synth", "SYNTHETIC RUN…");
    const body = {
      region: $("region") ? $("region").value : "global",
      country: $("country") ? $("country").value : "UTC",
      time_error_seconds: 0,
      aperture_m: 0.35,
      verbose: $("verboseToggle") ? $("verboseToggle").checked : true,
      nasa_compare: $("nasaCompare") ? $("nasaCompare").checked : true,
      mc_iterations: parseInt(($("mcIter") && $("mcIter").value) || "50", 10),
      injection_trials: parseInt(($("injectionN") && $("injectionN").value) || "28", 10),
      max_fidelity: $("maxFidelity") ? $("maxFidelity").checked : true,
      factory_mode: $("factoryMode") ? $("factoryMode").checked : true,
      use_vlbi: $("useVlbi") ? $("useVlbi").checked : true,
      use_nn: $("useNn") ? $("useNn").checked : false,
      resolution_preset: $("resolution") ? $("resolution").value : "auto",
      process_after: $("synthProcess") ? $("synthProcess").checked : true,
      random_time: true,
    };
    if (body.resolution_preset === "16K" && !confirm("16K may use several GB RAM and minutes. Continue?")) return;
    startJob("/api/synthetic", body, "SYNTH");
  });
  $("btnFactory").addEventListener("click", () => {
    const hasFile = !!filePath;
    let body;
    if (hasFile) {
      setModeBadge("real", "SELF-TEST · REAL…");
      body = payload({
        path: filePath,
        run_hard_synth: $("factoryHard") ? $("factoryHard").checked : true,
        hard_resolution: "1080p",
        hard_mc_iterations: 8,
        hard_injection_trials: 6,
      });
    } else {
      setModeBadge("factory", "SELF-TEST · SYNTH…");
      body = {
        region: $("region") ? $("region").value : "global",
        country: $("country") ? $("country").value : "UTC",
        aperture_m: 0.35,
        verbose: $("verboseToggle") ? $("verboseToggle").checked : true,
        nasa_compare: $("nasaCompare") ? $("nasaCompare").checked : true,
        mc_iterations: parseInt(($("mcIter") && $("mcIter").value) || "50", 10),
        injection_trials: parseInt(($("injectionN") && $("injectionN").value) || "28", 10),
        max_fidelity: $("maxFidelity") ? $("maxFidelity").checked : true,
        factory_mode: $("factoryMode") ? $("factoryMode").checked : true,
        use_vlbi: $("useVlbi") ? $("useVlbi").checked : true,
        resolution_preset: $("resolution") ? $("resolution").value : "1080p",
        run_hard_synth: $("factoryHard") ? $("factoryHard").checked : true,
        hard_resolution: "1080p",
        hard_mc_iterations: 8,
        hard_injection_trials: 6,
        random_time: true,
        user_time: ($("userTime") && $("userTime").value.trim()) || new Date().toISOString().slice(0, 19).replace("T", " "),
      };
    }
    if (!body) return;
    const msg = hasFile
      ? "Self-test will process YOUR uploaded file (plus multi-night / optional stress). Continue?"
      : "No file loaded — self-test will use synthetic images. Continue?";
    if (!confirm(msg)) return;
    startJob("/api/factory_night", body, "SELF-TEST");
  });
  $("btnMulti").addEventListener("click", () => startJob("/api/multi_epoch", { directory: null, smooth: true }, "MULTI"));
  $("btnHard").addEventListener("click", () => {
    setModeBadge("synth", "STRESS…");
    const body = payload({
      resolution: "1080p",
      seed: Date.now() % 100000,
      mc_iterations: parseInt($("mcIter").value || "10", 10),
      injection_trials: 8,
    });
    if (!body) return;
    startJob("/api/hard_synth", body, "HARD");
  });
  $("btnEph").addEventListener("click", async () => {
    const body = payload({});
    if (!body) return;
    setStatus("run", "EPH");
    try {
      const j = await (
        await fetch("/api/ephemeris", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
      ).json();
      if (!j.ok) return alert(j.error || "ephemeris failed");
      setText("ephBox", JSON.stringify(j.ephemeris, null, 2));
      const pe = j.ephemeris;
      setText(
        "ephSummary",
        `CM III=${fmt(pe.cm_iii_deg, 4)}° [${pe.cm_source}]  Δ=${fmt(pe.distance_au, 5)} AU  ` +
          `sublat=${fmt(pe.sub_obs_lat_deg, 3)}°  PA=${fmt(pe.north_pa_deg, 2)}°`
      );
      setText("dCm", pe.cm_source);
      showTab("eph", true);
      if (isMobileLayout()) setDrawer(false);
      setStatus("ok", "EPH OK");
    } catch (e) {
      alert(String(e));
      setStatus("err", "ERROR");
    }
  });
  $("btnWjTemplate")?.addEventListener("click", async () => {
    const j = await (await fetch("/api/winjupos/template")).json();
    alert(j.ok ? `Template written:\n${j.path}` : j.error || "failed");
  });
  $("btnClearLog")?.addEventListener("click", async () => {
    await fetch("/api/logs/clear", { method: "POST" });
    const box = $("console");
    if (box) box.innerHTML = "";
    lastLogId = 0;
    setText("logCount", "0");
  });
  $("verboseToggle").addEventListener("change", async () => {
    await fetch("/api/verbose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ verbose: $("verboseToggle").checked }),
    });
  });

  async function pollNn() {
    try {
      const j = await (await fetch("/api/nn/status")).json();
      const el = $("nnStatus");
      if (!el) return;
      if (j.running) {
        const mode = j.mode === "overnight" ? "OVERNIGHT" : "TRAIN";
        const best = j.best_loss != null ? Number(j.best_loss).toFixed(5) : "—";
        const nan = j.nan_skips != null ? j.nan_skips : 0;
        el.textContent =
          `NN ${mode}: epoch ${j.epoch}/${j.epochs || "…"}  loss=${j.loss != null ? Number(j.loss).toFixed(5) : "…"}  best=${best}  nan_skips=${nan}` +
          (j.strategy ? `  strat=${j.strategy}` : "") +
          (j.hours_left != null ? `  left=${Number(j.hours_left).toFixed(2)}h` : "") +
          (j.prevent_sleep ? "  · awake" : "");
        if ($("btnNnTrain")) $("btnNnTrain").disabled = true;
      } else {
        if ($("btnNnTrain") && !$("statusPill").classList.contains("run")) $("btnNnTrain").disabled = false;
        el.textContent = j.trained || j.weights_exist ? `NN ready · ${j.message || "weights on disk · NaN-guard on"}` : `NN idle · ${j.message || "not trained"}`;
      }
    } catch (_) {}
  }
  $("btnNnTrain")?.addEventListener("click", async () => {
    const overnight = $("nnOvernight") ? $("nnOvernight").checked : false;
    const body = {
      epochs: parseInt(($("nnEpochs") && $("nnEpochs").value) || "25", 10),
      samples_per_epoch: parseInt(($("nnSamples") && $("nnSamples").value) || "12", 10),
      fine_tune: $("nnFineTune") ? $("nnFineTune").checked : true,
      overnight,
      hours: parseFloat(($("nnHours") && $("nnHours").value) || "8"),
      prevent_sleep: $("nnPreventSleep") ? $("nnPreventSleep").checked : true,
      lr: 0.01,
      seed: Date.now() % 100000,
    };
    if (overnight && !confirm(`Start overnight SPIRE-Net for ~${body.hours}h?\nMac will try to stay awake (lid OK on power).\nNaN-guard will protect weights.`)) return;
    setStatus("run", overnight ? "NN OVERNIGHT" : "NN TRAIN");
    const j = await (
      await fetch("/api/nn/train", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
    ).json();
    if (!j.ok) alert(j.error || "train failed");
    else pollNn();
  });
  $("btnNnStop")?.addEventListener("click", async () => {
    await fetch("/api/nn/stop", { method: "POST" });
    pollNn();
  });

  function renderPillars(caps) {
    const bar = $("pillarBar");
    if (!bar || !caps || !caps.pillars) return;
    bar.innerHTML = "";
    const labels = {
      pro_ephemeris: "Eph",
      horizons_orientation: "Horizons",
      winjupos: "WJ",
      spice: "SPICE",
      vlbi_optical: "Stack",
      multi_epoch: "Multi",
      hard_synth: "Stress",
      factory_night: "Self-test",
      spire_net: "CNN",
      synthetic_hq: "Synth",
    };
    for (const [k, on] of Object.entries(caps.pillars)) {
      const s = document.createElement("span");
      s.className = "pillar " + (on ? "on" : "off");
      s.textContent = labels[k] || k;
      s.title = (labels[k] || k) + (on ? " available" : " off");
      bar.appendChild(s);
    }
  }

  function renderTips(tips) {
    const ul = $("tipsList");
    if (!ul) return;
    ul.innerHTML = "";
    (tips || []).forEach((t) => {
      const li = document.createElement("li");
      li.textContent = t;
      ul.appendChild(li);
    });
  }

  (async function init() {
    $("userTime").value = nowStamp();
    applyUserTimeToBar();
    try {
      const h = await (await fetch("/api/health")).json();
      $("healthInfo").textContent = `${h.app} v${h.version} · target ${h.target_arcsec}`;
      if (h.tips) renderTips(h.tips);
    } catch {
      $("healthInfo").textContent = "server offline — relaunch Launch_GRS_Observatory.command";
    }
    try {
      const tips = await (await fetch("/api/tips")).json();
      if (tips.tips) renderTips(tips.tips);
    } catch (_) {}
    try {
      const caps = await (await fetch("/api/capabilities")).json();
      renderPillars(caps);
      if (caps.winjupos_files && caps.winjupos_files.length) {
        $("winjuposInfo").textContent = "WinJUPOS on disk: " + caps.winjupos_files.join(", ");
      }
    } catch (_) {}
    try {
      const regs = await (await fetch("/api/regions")).json();
      const sel = $("region");
      if (sel) {
        sel.innerHTML = "";
        for (const [k, v] of Object.entries(regs)) {
          const o = document.createElement("option");
          o.value = k;
          o.textContent = `${k} — ${v}`;
          sel.appendChild(o);
        }
      }
    } catch (_) {}
    try {
      countries = await (await fetch("/api/countries")).json();
      const sel = $("country");
      if (sel) {
        sel.innerHTML = "";
        for (const [k, v] of Object.entries(countries)) {
          const o = document.createElement("option");
          o.value = k;
          o.textContent = `${k} — ${v.name}`;
          if (k === "UTC") o.selected = true;
          sel.appendChild(o);
        }
      }
      updateCountryHint();
    } catch (_) {}
    setInterval(poll, 600);
    setInterval(pollNn, 1200);
    pollNn();
  })();
})();
