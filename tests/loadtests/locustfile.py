"""
Load Testing Scenarios — Phase 18.6

Three user behaviors for locust load testing:
  - PublicUserBehavior: simulates visitor browsing (40% landing, 20% portfolio, etc.)
  - APIConsumerBehavior: simulates API client reading public endpoints
  - AdminBehavior: simulates admin dashboard + content editing

Set ``LOCUST_SKIP_ADMIN=1`` to omit AdminBehavior — used by the
``perf-regression`` CI job, which seeds a stub admin user without a
real password hash so authenticated traversal would 403 across the
board and trip the regression gate's failure-count check. The
production baseline (50 users × 5 min) runs without the skip and
exercises authenticated admin paths via a real password.

Usage:
  locust -f tests/loadtests/locustfile.py --headless -u 50 -r 5 -t 5m --host http://localhost:8080
"""

import os

from locust import HttpUser, between, task

SKIP_ADMIN = bool(os.environ.get('LOCUST_SKIP_ADMIN'))


class PublicUserBehavior(HttpUser):
    """Simulates a visitor browsing the public site."""

    wait_time = between(1, 3)
    weight = 5

    @task(40)
    def landing_page(self):
        self.client.get('/')

    @task(20)
    def portfolio(self):
        self.client.get('/portfolio')

    @task(20)
    def blog_index(self):
        self.client.get('/blog')

    @task(5)
    def services(self):
        self.client.get('/services')

    @task(5)
    def projects(self):
        self.client.get('/projects')

    @task(5)
    def testimonials(self):
        self.client.get('/testimonials')

    @task(5)
    def contact(self):
        self.client.get('/contact')


class APIConsumerBehavior(HttpUser):
    """Simulates an API consumer making read requests."""

    wait_time = between(0.5, 2)
    weight = 2

    @task(20)
    def site_metadata(self):
        self.client.get('/api/v1/site')

    @task(15)
    def services(self):
        self.client.get('/api/v1/services')

    @task(15)
    def stats(self):
        self.client.get('/api/v1/stats')

    @task(15)
    def portfolio(self):
        self.client.get('/api/v1/portfolio')

    @task(10)
    def portfolio_page2(self):
        self.client.get('/api/v1/portfolio?page=2&per_page=10')

    @task(10)
    def testimonials(self):
        self.client.get('/api/v1/testimonials')

    @task(10)
    def blog(self):
        self.client.get('/api/v1/blog')

    @task(5)
    def certifications(self):
        self.client.get('/api/v1/certifications')


class AdminBehavior(HttpUser):
    """Simulates an admin session browsing the dashboard and editing content.

    Disabled via ``abstract = True`` when ``LOCUST_SKIP_ADMIN`` is set in the
    environment. Locust treats abstract user classes as registration-only,
    so no instances are spawned and the /admin/* endpoints are absent from
    the stats CSV. ``regression_check.py`` then reports them as SKIPPED.
    """

    abstract = SKIP_ADMIN
    wait_time = between(2, 5)
    weight = 1

    @task(30)
    def dashboard(self):
        self.client.get('/admin/')

    @task(15)
    def photos(self):
        self.client.get('/admin/photos')

    @task(15)
    def services(self):
        self.client.get('/admin/services')

    @task(10)
    def blog_list(self):
        self.client.get('/admin/blog')

    @task(10)
    def settings(self):
        self.client.get('/admin/settings')

    @task(10)
    def reviews(self):
        self.client.get('/admin/reviews')

    @task(5)
    def stats(self):
        self.client.get('/admin/stats')

    @task(5)
    def search(self):
        self.client.get('/admin/search?q=test')
