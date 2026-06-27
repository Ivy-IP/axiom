// === Mobile nav toggle (now toggles .nav-bar) ===
const menuToggle = document.getElementById('menuToggle');
const navBar = document.getElementById('navCollapse');

if (menuToggle && navBar) {
  menuToggle.addEventListener('click', () => {
    navBar.classList.toggle('open');
    // animate hamburger
    menuToggle.classList.toggle('open');
  });
}

// === Notification bell dropdown ===
const bellBtn = document.getElementById('bellBtn');
const notifDropdown = document.getElementById('notifDropdown');

if (bellBtn && notifDropdown) {
  bellBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    notifDropdown.classList.toggle('open');
  });

  document.addEventListener('click', (e) => {
    if (!notifDropdown.contains(e.target) && !bellBtn.contains(e.target)) {
      notifDropdown.classList.remove('open');
    }
  });
}

// === Close mobile nav when a link is tapped ===
if (navBar) {
  navBar.querySelectorAll('a').forEach(a => {
    a.addEventListener('click', () => {
      navBar.classList.remove('open');
    });
  });
}
