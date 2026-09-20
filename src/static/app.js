/* TEMP: restoring functional SPA — full plaintext push next */
console.error('SPA app.js pending full 1.2.0 sync');
document.addEventListener('DOMContentLoaded',()=>{
  const el=document.getElementById('healthVersionText');
  if(el) el.textContent='SPA sync incomplete — reload after app.js push';
});
