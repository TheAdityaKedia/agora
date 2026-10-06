/**
 * Masala Comedy Club — Site Configuration
 *
 * Central config for external service URLs.
 * Update URLs here without modifying HTML files.
 */
const SITE_CONFIG = {
  YOUTUBE_CHANNEL: 'https://www.youtube.com/@MasalaComedy',
  FACEBOOK_PAGE: 'https://www.facebook.com/masalacc',
  INSTAGRAM_PAGE: 'https://www.instagram.com/masalacc/',
  GOOGLE_FORM_URL: 'https://forms.gle/bF5brawxxeFXSw7C7',
  // Mailchimp audience signup endpoint (the form's POST action). Used by the
  // mailing-list forms on the homepage + Contact Us.
  // Honeypot field name: b_131c9f48e753469ee4ad3bdfa_244a3b410f
  MAILING_LIST_ACTION: 'https://eepurl.us20.list-manage.com/subscribe/post?u=131c9f48e753469ee4ad3bdfa&id=244a3b410f&f_id=00ab84e6f0',
  // Meta (Facebook) Pixel. analytics.js reads this and installs the base code —
  // it is NOT hardcoded in the page <head> any more, so this is the only place
  // the ID lives. Previous pixel: 797889526307268 (retired Aug 2026).
  FB_PIXEL_ID: '2116260272256905',
  GA4_MEASUREMENT_ID: 'G-X4Y8SS4Y37',
  SUPPORT_EMAIL: 'web@masalacc.org',
  CONTACT_EMAIL: 'info@masalacc.org',

  // 🔧 Live event ticket IDs (Tugoz). To swap an event, update the ID here.
  // The key must match the data-event-key on the page's <div id="tugoz-embed">.
  LIVE_EVENTS: {
    lt10: 113920,
    openmic: 112933,
  }
};
