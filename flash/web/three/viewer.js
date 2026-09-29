/* Flash's 3D model viewer: draws a .glb, .stl or .obj file in a box,
   lit like a studio, sitting on a soft shadow, to turn with the mouse.
   The web UI's panel and the headless preview the agent looks at both
   use this, so what the agent checks is what the user sees. Needs
   three.min.js, which sets window.FlashThree, loaded first. */
(function () {
  "use strict";

  const SKY = [1, 0.7, 1];  // where the camera starts, looking at the model

  function parse(T, data, suffix) {
    return new Promise((resolve, reject) => {
      if (suffix === ".glb") {
        new T.GLTFLoader().parse(data, "", (gltf) => resolve(gltf.scene), reject);
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
        resolve(group);
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
        resolve(group);
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
    const model = await parse(T, data, String(suffix || "").toLowerCase());

    const renderer = new THREE.WebGLRenderer({
      antialias: true, alpha: true, preserveDrawingBuffer: !!opts.still,
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
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

    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
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
    scene.add(new THREE.HemisphereLight(0xffffff, 0x888888, 0.4));

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
      camera.position.copy(centre).addScaledVector(toward, fit * 0.95);
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

    function tick() {
      if (!alive) return;
      frame = requestAnimationFrame(tick);
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
      spin(on) { controls.autoRotate = !!on; },
      wireframe(on) {
        model.traverse((node) => {
          if (!node.isMesh) return;
          for (const m of [].concat(node.material)) m.wireframe = !!on;
        });
      },
      dispose() {
        alive = false;
        cancelAnimationFrame(frame);
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

  window.FlashModelView = { show };
})();
