/* Flash's 3D model viewer: draws a .glb, .stl or .obj file in a box,
   lit like a studio, sitting on a soft shadow, to turn with the mouse.
   The web UI's panel and the headless preview the agent looks at both
   use this, so what the agent checks is what the user sees. Needs
   three.min.js, which sets window.FlashThree, loaded first. */
(function () {
  "use strict";

  // Where the camera starts: in front (glTF faces +Z), a little right
  // and above, so a front reads straight on and the model still has depth.
  const SKY = [0.45, 0.5, 1];

  function parse(T, data, suffix) {
    return new Promise((resolve, reject) => {
      if (suffix === ".glb") {
        new T.GLTFLoader().parse(data, "", (gltf) => resolve({ root: gltf.scene, clips: gltf.animations || [] }), reject);
        return;
      }
      if (suffix === ".stl") {
        const geometry = new T.STLLoader().parse(data);
        geometry.computeVertexNormals();
        const material = new T.THREE.MeshStandardMaterial({
          color: geometry.hasColors ? 0xffffff : 0xb0b4bb,
          vertexColors: !!geometry.hasColors,
          roughness: 0.55,
          metalness: 0.05,
        });
        const mesh = new T.THREE.Mesh(geometry, material);
        // STL files are made Z up, for printing; turn it to Y up.
        mesh.rotation.x = -Math.PI / 2;
        const group = new T.THREE.Group();
        group.add(mesh);
        resolve({ root: group, clips: [] });
        return;
      }
      if (suffix === ".obj") {
        const text = new TextDecoder().decode(data);
        const group = new T.OBJLoader().parse(text);
        group.traverse((node) => {
          if (node.isMesh && (!node.material || node.material.type === "MeshPhongMaterial")) {
            node.material = new T.THREE.MeshStandardMaterial({ color: 0xb0b4bb, roughness: 0.55 });
          }
        });
        resolve({ root: group, clips: [] });
        return;
      }
      reject(new Error(`cannot show ${suffix} files`));
    });
  }

  function stats(root) {
    let meshes = 0;
    let triangles = 0;
    root.traverse((node) => {
      if (!node.isMesh || !node.geometry) return;
      meshes += 1;
      const g = node.geometry;
      triangles += Math.floor((g.index ? g.index.count : g.attributes.position.count) / 3);
    });
    return { meshes, triangles };
  }

  /* Draw DATA (an ArrayBuffer, or a URL to fetch) of type SUFFIX in
     HOLDER. Resolves to a handle: reset() puts the camera back,
     wireframe(on) shows the edges, spin(on) turns the model slowly, and
     dispose() lets go of the GPU once the panel shows something else.
     A model that moves plays as it opens, every motion looping on its
     own: play(on) pauses and resumes it, speed(x) sets how fast, and
     time() and seek(seconds) read and set where it is, up to duration.
     OPTIONS: light (a light background), still (draw once, for a
     picture, with no animation). */
  async function show(holder, data, suffix, options) {
    const opts = options || {};
    const T = window.FlashThree;
    if (!T) throw new Error("three.js did not load");
    const THREE = T.THREE;
    if (typeof data === "string") {
      const response = await fetch(data, { credentials: "same-origin" });
      if (!response.ok) throw new Error(response.statusText || `HTTP ${response.status}`);
      data = await response.arrayBuffer();
    }
    const parsed = await parse(T, data, String(suffix || "").toLowerCase());
    const model = parsed.root;

    const renderer = new THREE.WebGLRenderer({
      antialias: true, alpha: true, preserveDrawingBuffer: !!opts.still,
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    // Neutral keeps a colour the colour it was given; ACES bleaches greys.
    renderer.toneMapping = THREE.NeutralToneMapping;
    renderer.toneMappingExposure = 1;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.domElement.style.display = "block";
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    renderer.domElement.style.touchAction = "none";
    holder.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const pmrem = new THREE.PMREMGenerator(renderer);
    const environment = pmrem.fromScene(new T.RoomEnvironment(), 0.04).texture;
    scene.environment = environment;
    // Soft enough that a face turned to the viewer shows about its own
    // colour, rather than a paler one.
    scene.environmentIntensity = 0.5;
    pmrem.dispose();

    model.traverse((node) => {
      if (node.isMesh) { node.castShadow = true; node.receiveShadow = true; }
    });
    scene.add(model);

    // Measure it, and set it on the ground where its lowest point is.
    const box = new THREE.Box3().setFromObject(model);
    if (box.isEmpty()) throw new Error("the model has nothing in it to draw");
    const size = box.getSize(new THREE.Vector3());
    const centre = box.getCenter(new THREE.Vector3());
    const radius = Math.max(size.length() / 2, 1e-3);

    const sun = new THREE.DirectionalLight(0xffffff, 1.0);
    sun.position.set(centre.x + radius * 1.5, box.max.y + radius * 3, centre.z + radius * 2);
    sun.target.position.copy(centre);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.camera.left = sun.shadow.camera.bottom = -radius * 2;
    sun.shadow.camera.right = sun.shadow.camera.top = radius * 2;
    sun.shadow.camera.near = radius * 0.1;
    sun.shadow.camera.far = radius * 10;
    sun.shadow.bias = -0.0005;
    sun.shadow.normalBias = radius * 0.004;
    scene.add(sun, sun.target);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x666666, 0.25));

    const light = opts.light !== undefined ? opts.light
      : document.documentElement.dataset.theme === "light";
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(radius * 12, radius * 12),
      new THREE.ShadowMaterial({ opacity: light ? 0.18 : 0.35 }),
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(centre.x, box.min.y - radius * 0.001, centre.z);
    floor.receiveShadow = true;
    scene.add(floor);

    const step = Math.pow(10, Math.floor(Math.log10(radius)));
    const cells = Math.max(4, Math.min(40, Math.round((radius * 6) / step)));
    const grid = new THREE.GridHelper(step * cells, cells, light ? 0xc8c8cc : 0x4a4a50, light ? 0xdedee2 : 0x34343a);
    grid.position.set(centre.x, box.min.y, centre.z);
    grid.material.transparent = true;
    grid.material.opacity = 0.6;
    grid.material.depthWrite = false;
    scene.add(grid);

    const camera = new THREE.PerspectiveCamera(40, 1, radius / 100, radius * 100);
    const controls = new T.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = radius * 0.2;
    controls.maxDistance = radius * 20;

    function reset() {
      const toward = new THREE.Vector3(...SKY).normalize();
      // Far enough back that the whole model fits the narrower way.
      const high = THREE.MathUtils.degToRad(camera.fov / 2);
      const wide = Math.atan(Math.tan(high) * camera.aspect);
      const fit = radius / Math.sin(Math.min(high, wide));
      camera.position.copy(centre).addScaledVector(toward, fit * 1.1);
      controls.target.copy(centre);
      camera.lookAt(centre);
      controls.update();
    }

    let width = 0;
    let height = 0;
    function resize() {
      const w = Math.max(1, holder.clientWidth);
      const h = Math.max(1, holder.clientHeight);
      if (w === width && h === height) return;
      width = w;
      height = h;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
    resize();
    reset();

    let frame = 0;
    let alive = true;
    const watcher = typeof ResizeObserver === "function" ? new ResizeObserver(resize) : null;
    if (watcher) watcher.observe(holder);
    // It turns slowly on its own until someone takes hold of it.
    controls.autoRotate = !opts.still;
    controls.autoRotateSpeed = 1.2;
    controls.addEventListener("start", () => { controls.autoRotate = false; });

    // Its motion: every clip at once, each looping at its own length.
    const mixer = parsed.clips.length ? new THREE.AnimationMixer(model) : null;
    const duration = Math.max(0, ...parsed.clips.map((clip) => clip.duration));
    let playing = !!mixer && !opts.still;
    let rate = 1;
    if (mixer) {
      for (const clip of parsed.clips) mixer.clipAction(clip).play();
      mixer.setTime(0);
    }
    const clock = new THREE.Clock();
    const touch = opts.still ? null : interactive(T, THREE, holder, renderer, camera, model);

    function tick() {
      if (!alive) return;
      frame = requestAnimationFrame(tick);
      const delta = Math.min(clock.getDelta(), 0.1);
      if (mixer && playing) mixer.update(delta * rate);
      if (touch) touch.update(delta);
      controls.update();
      renderer.render(scene, camera);
    }
    if (opts.still) renderer.render(scene, camera); else tick();

    const counted = stats(model);
    return {
      size: [size.x, size.y, size.z],
      meshes: counted.meshes,
      triangles: counted.triangles,
      reset,
      animated: !!mixer,
      // How many parts say what they are, and how many move when clicked.
      told: touch ? touch.told : 0,
      clickable: touch ? touch.clickable : 0,
      // Click a part by its name, as the user would: true if it moved.
      click(name) { return touch ? touch.toggle(name) : false; },
      duration,
      play(on) { playing = !!mixer && !!on; },
      get playing() { return playing; },
      speed(x) { rate = Math.max(0.05, Number(x) || 1); },
      time() { return mixer && duration ? mixer.time % duration : 0; },
      seek(seconds) { if (mixer) mixer.setTime(Math.max(0, Number(seconds) || 0)); },
      spin(on) { controls.autoRotate = !!on; },
      wireframe(on) {
        model.traverse((node) => {
          if (!node.isMesh) return;
          for (const m of [].concat(node.material)) m.wireframe = !!on;
        });
      },
      dispose() {
        alive = false;
        if (touch) touch.dispose();
        cancelAnimationFrame(frame);
        if (mixer) mixer.stopAllAction();
        if (watcher) watcher.disconnect();
        controls.dispose();
        scene.traverse((node) => {
          if (node.geometry) node.geometry.dispose();
          for (const m of [].concat(node.material || [])) {
            for (const key in m) if (m[key] && m[key].isTexture) m[key].dispose();
            m.dispose();
          }
        });
        environment.dispose();
        renderer.dispose();
        renderer.forceContextLoss();
        renderer.domElement.remove();
      },
    };
  }

  /* Parts that answer the pointer: the model put what each says about
     itself, and what it does when clicked, in its glTF extras (read as
     userData.flash). Pointing at one lights it and shows its name and
     info; clicking one that moves plays its move, and the next click
     plays it back. A tap does on a phone what pointing does with a mouse. */
  function interactive(T, THREE, holder, renderer, camera, model) {
    const parts = [];
    model.traverse((node) => { if (node.userData && node.userData.flash) parts.push(node); });
    if (!parts.length) return null;
    if (getComputedStyle(holder).position === "static") holder.style.position = "relative";
    const card = document.createElement("div");
    card.className = "model-part-card";
    card.hidden = true;
    Object.assign(card.style, { position: "absolute", pointerEvents: "none", zIndex: 2, maxWidth: "260px" });
    holder.appendChild(card);
    const deg = Math.PI / 180;
    // each part's own materials, so lighting one up lights only it
    for (const part of parts) {
      part.traverse((n) => {
        if (!n.isMesh) return;
        const own = [].concat(n.material).map((m) => m.clone());
        n.material = own.length > 1 ? own : own[0];
      });
      const f = part.userData.flash;
      if (f.click) {
        part.userData.rest = { position: part.position.clone(), quaternion: part.quaternion.clone() };
        part.userData.amount = 0;
        part.userData.target = 0;
      }
    }
    const caster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let hovered = null;
    function partAt(event) {
      const box = renderer.domElement.getBoundingClientRect();
      pointer.set(((event.clientX - box.left) / box.width) * 2 - 1, -((event.clientY - box.top) / box.height) * 2 + 1);
      caster.setFromCamera(pointer, camera);
      for (const hit of caster.intersectObject(model, true)) {
        for (let node = hit.object; node; node = node.parent) if (node.userData && node.userData.flash) return node;
      }
      return null;
    }
    function glow(part, on) {
      part.traverse((n) => {
        if (!n.isMesh) return;
        for (const m of [].concat(n.material)) {
          if (!m.emissive) continue;
          if (on) { m.userData.was = m.emissive.clone(); m.emissive.setRGB(0.1, 0.11, 0.15); }
          else if (m.userData.was) m.emissive.copy(m.userData.was);
        }
      });
    }
    function show(part, event) {
      const f = part.userData.flash;
      card.innerHTML = "";
      const name = document.createElement("b");
      name.textContent = f.name || part.name;
      card.appendChild(name);
      if (f.info) { const said = document.createElement("span"); said.textContent = f.info; card.appendChild(said); }
      if (f.click) { const how = document.createElement("em"); how.textContent = part.userData.target ? "Click to put it back" : "Click to move it"; card.appendChild(how); }
      const box = holder.getBoundingClientRect();
      card.hidden = false;
      const x = Math.min(event.clientX - box.left + 14, box.width - card.offsetWidth - 8);
      const y = Math.max(8, event.clientY - box.top - card.offsetHeight - 10);
      card.style.left = `${Math.max(8, x)}px`;
      card.style.top = `${y}px`;
    }
    function point(part, event) {
      if (part !== hovered) {
        if (hovered) glow(hovered, false);
        hovered = part;
        if (part) glow(part, true);
      }
      renderer.domElement.style.cursor = part && part.userData.flash.click ? "pointer" : "";
      if (part) show(part, event); else card.hidden = true;
    }
    function toggle(which) {
      const part = typeof which === "string" ? parts.find((p) => (p.userData.flash.name || p.name) === which) : which;
      if (!part || !part.userData.flash.click) return false;
      part.userData.target = part.userData.target ? 0 : 1;
      return true;
    }
    let down = null;
    const el = renderer.domElement;
    const onMove = (event) => { if (event.pointerType === "mouse" && !event.buttons) point(partAt(event), event); };
    const onDown = (event) => { down = { x: event.clientX, y: event.clientY }; };
    const onUp = (event) => {
      if (!down || Math.hypot(event.clientX - down.x, event.clientY - down.y) > 5) { down = null; return; }
      down = null;
      const part = partAt(event);
      if (part) toggle(part);
      point(part, event);
      if (event.pointerType !== "mouse") setTimeout(() => { if (hovered === part) point(null, event); }, 3500);
    };
    const onLeave = () => { if (hovered) glow(hovered, false); hovered = null; card.hidden = true; };
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointerleave", onLeave);
    const turned = new THREE.Quaternion();
    const euler = new THREE.Euler();
    const offset = new THREE.Vector3();
    return {
      told: parts.filter((p) => p.userData.flash.info).length,
      clickable: parts.filter((p) => p.userData.flash.click).length,
      toggle,
      update(delta) {
        for (const part of parts) {
          const c = part.userData.flash.click;
          if (!c || part.userData.amount === part.userData.target) continue;
          const step = delta / Math.max(0.05, c.seconds);
          const a = part.userData.amount = part.userData.target > part.userData.amount
            ? Math.min(1, part.userData.amount + step) : Math.max(0, part.userData.amount - step);
          const k = a < 0.5 ? 2 * a * a : 1 - Math.pow(-2 * a + 2, 2) / 2;   // smooth in and out
          const rest = part.userData.rest;
          euler.set(c.turn[0] * deg * k, c.turn[1] * deg * k, c.turn[2] * deg * k);
          turned.setFromEuler(euler);
          const pivot = new THREE.Vector3(...c.pivot);
          offset.copy(rest.position).sub(pivot).applyQuaternion(turned);
          part.position.copy(pivot).add(offset).add(new THREE.Vector3(...c.move).multiplyScalar(k));
          part.quaternion.copy(turned).multiply(rest.quaternion);
        }
      },
      dispose() {
        el.removeEventListener("pointermove", onMove);
        el.removeEventListener("pointerdown", onDown);
        el.removeEventListener("pointerup", onUp);
        el.removeEventListener("pointerleave", onLeave);
        card.remove();
      },
    };
  }

  window.FlashModelView = { show };
})();
