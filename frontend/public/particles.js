(function () {
  var canvas = document.getElementById('particles');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var particles = [];
  var NUM        = 80;
  var LINK_DIST  = 120;
  var RAD        = 2;

  // Uebernommen aus mh_web_config. Dort schaltet die Palette mit
  // data-bs-theme um; der Builder ist durchgaengig dunkel.
  function colors() {
    return {
      dot:  'rgba(46,125,94,0.90)',
      line: 'rgba(46,125,94,0.15)',
    };
  }

  function resize() {
    canvas.width  = window.innerWidth;
    canvas.height = window.innerHeight;
    if (particles.length === 0) {
      for (var i = 0; i < NUM; i++) {
        particles.push({
          x:  Math.random() * canvas.width,
          y:  Math.random() * canvas.height,
          vx: (Math.random() - 0.5) * 0.8,
          vy: (Math.random() - 0.5) * 0.8,
        });
      }
    }
  }

  function draw() {
    if (!ctx || !canvas.width) return;
    var c = colors();
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // move & bounce
    for (var i = 0; i < particles.length; i++) {
      var p = particles[i];
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < 0 || p.x > canvas.width)  p.vx *= -1;
      if (p.y < 0 || p.y > canvas.height) p.vy *= -1;
      p.x = Math.max(0, Math.min(canvas.width,  p.x));
      p.y = Math.max(0, Math.min(canvas.height, p.y));
    }

    // lines
    for (var i = 0; i < particles.length; i++) {
      for (var j = i + 1; j < particles.length; j++) {
        var dx = particles[i].x - particles[j].x;
        var dy = particles[i].y - particles[j].y;
        var d  = Math.sqrt(dx * dx + dy * dy);
        if (d < LINK_DIST) {
          ctx.strokeStyle  = c.line;
          ctx.globalAlpha  = 1 - d / LINK_DIST;
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.stroke();
          ctx.globalAlpha  = 1;
        }
      }
    }

    // dots
    ctx.fillStyle = c.dot;
    for (var i = 0; i < particles.length; i++) {
      ctx.beginPath();
      ctx.arc(particles[i].x, particles[i].y, RAD, 0, Math.PI * 2);
      ctx.fill();
    }

    requestAnimationFrame(draw);
  }

  resize();
  window.addEventListener('resize', resize);
  requestAnimationFrame(draw);
}());
