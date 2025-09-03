# Somerset Chess Scheduler

A comprehensive Django-based attendance management system for chess coaching programs.

## Features

- 📅 **Smart Scheduling**: Intelligent lesson scheduling with conflict detection
- 👥 **Fill-in Management**: Automated fill-in student suggestions based on attendance history
- 📊 **Progress Tracking**: Visual indicators for student progress and attendance patterns
- 🔔 **Missed Lesson Alerts**: Proactive notifications for incomplete attendance records
- 📱 **Mobile Responsive**: Optimized for tablets and mobile devices
- 🔒 **Secure**: Production-ready with proper authentication and security measures

## Quick Start

### Local Development

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd somerset-chess-app
   ```

2. **Set up virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up database**
   ```bash
   # Create PostgreSQL database
   createdb somerset_chess

   # Run migrations
   python manage.py migrate

   # Create superuser
   python manage.py createsuperuser
   ```

5. **Run development server**
   ```bash
   python manage.py runserver
   ```

## Deployment to Render

### Prerequisites
- GitHub account
- Render account (free tier available)

### Step-by-Step Deployment

1. **Push code to GitHub**
   ```bash
   git add .
   git commit -m "Ready for production"
   git push origin main
   ```

2. **Create Render Web Service**
   - Go to [Render Dashboard](https://dashboard.render.com)
   - Click "New" → "Web Service"
   - Connect your GitHub repository
   - Configure the service:
     - **Name**: somerset-chess-scheduler
     - **Environment**: Python
     - **Build Command**: `pip install -r requirements.txt`
     - **Start Command**: `gunicorn somerset_project.wsgi:application`

3. **Set Environment Variables**
   In Render dashboard, add these environment variables:
   ```
   DEBUG=False
   SECRET_KEY=your-super-secret-key-here
   ALLOWED_HOSTS=your-app-name.onrender.com
   DJANGO_SETTINGS_MODULE=somerset_project.settings
   ```
   > **Note**: Render automatically provides `DATABASE_URL`

4. **Deploy**
   - Click "Create Web Service"
   - Wait for deployment to complete
   - Your app will be available at `https://your-app-name.onrender.com`

5. **Post-Deployment Setup**
   ```bash
   # Run migrations (via Render shell or dashboard)
   python manage.py migrate

   # Create superuser
   python manage.py createsuperuser

   # Collect static files
   python manage.py collectstatic --noinput
   ```

## Environment Variables

Create a `.env` file for local development:

```bash
# Django Configuration
DEBUG=True
SECRET_KEY=your-development-secret-key
ALLOWED_HOSTS=localhost,127.0.0.1

# Database (local)
DATABASE_URL=postgresql://user:password@localhost:5432/somerset_chess
```

## Key Features

### Intelligent Fill-in System
- **Priority Ordering**: Students with fewest lessons appear first
- **Progress Indicators**: Color-coded badges showing attendance status
- **Smart Filtering**: Available students only, respecting scheduling conflicts

### Dashboard Insights
- **Missed Lessons Alert**: Shows pending attendance records
- **Quick Navigation**: Direct links to problematic lessons
- **Calendar Integration**: Visual calendar with attendance data

### Admin Features
- **User Management**: Coach accounts with role-based access
- **Data Import**: Bulk student and schedule management
- **Reporting**: Attendance analytics and export capabilities

## API Endpoints

- `GET /` - Dashboard
- `GET /admin/` - Django Admin Panel
- `GET /health/` - Health check endpoint
- `POST /manage-lesson/<id>/` - Fill-in management

## Security Features

- ✅ CSRF protection
- ✅ SQL injection prevention
- ✅ XSS protection
- ✅ Secure headers (in production)
- ✅ HTTPS enforcement
- ✅ Session security

## Troubleshooting

### Common Issues

1. **Static files not loading**
   ```bash
   python manage.py collectstatic --noinput
   ```

2. **Database connection issues**
   - Check `DATABASE_URL` format
   - Verify PostgreSQL credentials

3. **Migration errors**
   ```bash
   python manage.py showmigrations
   python manage.py migrate --fake-initial
   ```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For support or questions:
- Create an issue in the repository
- Check the documentation
- Review the troubleshooting section

---

**Ready to deploy?** Follow the Render deployment steps above and your chess scheduling system will be live in minutes! 🎯
